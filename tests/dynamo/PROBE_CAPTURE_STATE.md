# Stage A capture-state probe

`probe_capture_state.py` answers five questions about view state, in Revit and
with evidence, **before** any capture code changes. The offline half is
`tools/analyze_capture_state_probe.py`.

**UNVERIFIED.** No one has run this probe in Revit yet. It was smoke-run only
against a throwaway fake of the Revit API, which checks that the Python runs
end to end and that the post-rollback verify reports a field left changed. It
does not show that any Revit call behaves as assumed. Treat its first run as
the test of the probe itself.

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
  write. If no shape is set, the removal is recorded as `unavailable`.
- **S4:** S2 and S3 together.

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
  the set (value or raised) and the read-back. `took_effect` is the read-back
  being FlatColors: a set that does not raise but does not take effect is
  possible.
- **S1:** a separate nested group. Detaches the template
  (`ViewTemplateId = InvalidElementId`), then sets FlatColors again.
- **Also recorded:** `GetPrimaryViewId` and whether it is valid; the template's
  `GetNonControlledTemplateParameterIds()` count; and whether
  `MODEL_GRAPHICS_STYLE` (Visual Style) is among the controlled parameters.

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

## How to run

1. Open the model with the views below. Dynamo 3.x, CPython3 Python node.
2. Paste `tests/dynamo/probe_capture_state.py` into the node, or load it with
   `exec(open(path).read())`. It imports nothing from the repository.
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
| `IN[8]` | questions: `"all"` or a comma list of `q1_q2,q3,q4,q5` | `"all"` | optional |

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
  - `questions.q1_q2 / q3 / q4 / q5`: the steps above, each with its writes,
    common reads and export record
  - `exports`: every export with its step, file, requested pixel size, fit
    direction, and `dims_px` read back from the TIFF header (stdlib `struct`)
  - `transaction_group`: whether the outer group started and rolled back
  - `restore_checks` per question (per view for Q3 and Q5), and refused steps
    with `refused`, `refused_reason` and `blocked_by`
  - `verify`: per view and field, `equal`, `changed`, `unverifiable` or
    `unreadable_both`, with before and after
  - `verify_changed`, `verify_unreadable_both` and `VERIFY_SUMMARY`
  - `exceptions`: every exception, with its stage and traceback
- **`<step>_<viewid>.tiff`**, e.g. `Q1_S2_6112047.tiff`, `Q3_S4_11999340.tiff`
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

The probe imports nothing from `vop_interwoven`, so it can be pasted into a
node with no repository path set up. The minimum it needs is reimplemented:
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
