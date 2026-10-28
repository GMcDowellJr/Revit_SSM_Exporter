# Change-detection probe (Q1)

`probe_change_detection.py` answers one question with measurements, before
anything is designed: **can Revit tell us cheaply which elements changed
between two captured model versions, so a later run can skip the views that
did not change?** The offline half is `tools/analyze_change_detection_probe.py`,
which checks the API's answer against captures already made.

**Q1 answered (run `20261026T095313`, OBSERVED):** `GetChangedElements`
cannot compare two archive versions as the batch opens them. Two detached
opens of one file got two version GUIDs (`NumberOfSaves: 1` each), and B
asked about either A GUID raised `ArgumentException: This GUID is invalid in
the given document`, exactly as the random-GUID negative control did; B's own
GUID succeeded (0 / 0 / 0, 1.5 ms). Archives unchanged. Q1b below asks the
same question of `Element.VersionGuid`.

**Q1b (probe `2026-10-27.1`) is UNVERIFIED in Revit.** Only the pure parts are tested offline
(`tests/test_analyze_change_detection_probe.py`: the sample, the signature
check, the per-view signals, the archive fingerprint, and the analyzer end to
end on synthetic runs). Every Revit call is the question. Treat the first run
as the test of the probe itself.

## What is assumed, and how the probe checks it

The Revit 2023+ API is BELIEVED to have:

- `Document.GetDocumentVersion(Document)`, static, returning a
  `DocumentVersion` with `VersionGUID` and `NumberOfSaves`;
- `Document.GetChangedElements(Guid)`, returning a `ChangedElements` with
  `GetCreatedElementIds()`, `GetModifiedElementIds()` and
  `GetDeletedElementIds()`.

None of that is taken from documentation. Step 1 reflects over the loaded
`RevitAPI` assembly and records every overload of both names, with return and
parameter types, plus every method and property of the types they return
(`reflection.return_types`). **Q1 stops, before anything is opened,** when
either member is absent or has no overload of the shape above
(`reflection.<name>.reason`, `stopped`). A one-parameter enum overload of
`GetModifiedElementIds`, if reflection shows one, is called for every enum
value (`by_change_type`); otherwise that field says it was not there.

## The open question it answers

The batch opens every archive copy `DetachAndPreserveWorksets`. Does a
detached copy keep a version history that a later copy's GUID can be found
in, or does detaching start a new history?

The archives are copies of **cloud-workshared centrals** (`basic_file_info`
of run `20261026T084944`: `IsCentral`, `CentralPath: Autodesk Docs://...`).
A local copy of one cannot be opened attached: in that run the open of A's
copy, not detached, never returned (Revit idle, about 18 GB). So the control
is a second DETACHED open of A (`control: "reopen"`, the default):

| call / field | what it separates |
|---|---|
| `reopen_same_guid` | `false`: each detached open mints its own version GUID |
| `b_detached__vs__a_detached` | the batch's situation: both detached |
| `b_detached__vs__a_detached_reopen` | the same question, asked about A's other detached GUID |
| `b_detached__vs__own_guid_positive_control` | B asked about its own version: must succeed |
| `b_detached__vs__random_guid_negative_control` | a seeded random GUID: what "not in this history" raises |

How to read it (INFERENCE until the run):

- `reopen_same_guid: false`: a detached open is a new version every time,
  so no later detached open can find it in its history. Run `084944` read
  A detached with `NumberOfSaves: 1`, which points that way.
- `b_detached__vs__a_detached` raises like the negative control: the batch's
  detached captures cannot be compared through this API.
- It succeeds: detaching keeps the lineage, and the per-view part measures
  what the API says.
- The positive control raises: `GetChangedElements` does not work on a
  detached document at all, and nothing else in the run means anything.

`control: "copy"` (A and B opened NOT detached from byte copies) remains for
an archive that is not a cloud central; for a cloud central it is refused
before anything is copied (`opens.copy_control_refused`, with both central
paths).

`versions.*.saves` (`NumberOfSaves`) is recorded for every open: B's count
above A's is consistent with one lineage, never proof of it.

## Q1b: Element.VersionGuid

`Element.VersionGuid` is BELIEVED to be a per-element id that changes when
the element is modified. Reflection lists every `Element` property with
"Version" in its name (`reflection.element_version_properties`) and Q1b runs
only when `VersionGuid` is among them (`version_guid_run`).

In each DETACHED open (A, A reopened, B) every element's and element type's
`VersionGuid` is read once (`opens.<label>.version_guids`: count, errors,
ms). Three answers, in order of what they decide:

| field | question | unusable when |
|---|---|---|
| `reopen_version_guids` | one file opened detached twice: the same VersionGuids? | `differ > 0`: a detached open re-mints them |
| `version_guid_a_vs_b` | A against B, by element id: same, differ, only in A, only in B | `same == 0`: no lineage across archives |
| `views[].vg_verdict` | per view: did anything it depends on change? | many false unchanged in the analyzer |

Per view the fingerprint covers the view, its members, its template, its and
its template's filters, and its members' types, each as `id:VersionGuid`
(`views[].version_guid.fingerprint_a / _b`). The signals say which part
moved: members added, removed or changed, the view itself, the template (a
different one, or the same one changed), filters, member types.
`vg_verdict` is `unknown`, never `unchanged`, when any input was not read.
Unchanged is exactly "the two fingerprints are equal"
(`test_unchanged_is_exactly_equal_fingerprints_over_generated_inputs`).

Known blind spots (INFERENCE until the analyzer measures them): a change
inside a linked model; an object-style (category) change; anything the view
draws that is not one of its member elements.

### Q1b, run `20261028T111442` (probe `2026-10-27.1`, V56 -> V57), OBSERVED

- `Element.VersionGuid` exists (`System.Guid VersionGuid`); read for all
  446,169 elements of A in 4.4 s, 449,270 of B in 4.9 s, no errors.
- **Not fully stable:** two detached opens of A gave 1,937 elements (0.43 %)
  a different VersionGuid. Both opens deleted 386 warnings and answered
  Elements_Lost_On_Import; that open-time repair re-minting them is
  INFERENCE.
- A -> B: 437,457 the same, 5,203 different, 3,509 only in A, 6,610 only in
  B.
- 50 views sampled. `vg_verdict` unchanged for 15, changed for 35. Against
  the 10-21 captures, with the capture's own tick and frame lines set aside
  (analyzer 1.2.0; provisional until rerun): unchanged and capture same 15,
  **false unchanged 0**; changed and capture differs 27; changed and capture
  same 7; not comparable 1.
- Cost per view: membership read median 135 ms (max 3.2 s), member types
  2 ms, fingerprint < 1 ms.

### Probe `2026-10-28.1`: the stable verdict

The 1,937 re-minted elements (`volatile_ids`, the reopen's differing ids)
mark views changed that did not change. The probe now records them
(`reopen_version_guids.volatile_ids`, first 200, and
`volatile_by_category`) and gives each view a second verdict,
`vg_verdict_stable`, with their VersionGuids left out of the comparison
(membership is still compared). Analyzer 1.3.0 tabulates it too
(`table_version_guid_stable`, `false_unchanged_version_guid_stable`).
Unchanged is exactly "the stable fingerprints are equal", asserted over
generated inputs. It needs `control: "reopen"` (the default).

The sample now draws: run `095313` drew 0 views because production's
`batch_capture._view_type_name` returned NUMBERS under Dynamo ("1", "117"),
so `select_views` kept nothing (`sample.production_select_views` records it
again). The probe resolves a numeric type through `Enum.GetName`
(`resolve_view_type_name`; `sample.type_name_read_by` counts how each was
read). Production is unchanged; the batch runs in pyRevit, where the
selection works.

### Q1b, run `20261028T114224` (probe `2026-10-28.1`, analyzer 1.3.0), OBSERVED

Same pair (V56 -> V57), same 50 views; archives unchanged; 4.5 minutes.

- The re-mint is deterministic: again 1,937 of 446,169. By category: Rooms
  469, uncategorised Elements 404, Room Tags 323, Specialty Equipment 230,
  Dimensions 229, Plumbing Fixtures 90, a DWG's element types 83, and smaller
  counts (Lighting Fixtures, Spot Elevations, Model Groups, RVT link
  instances and types, Schedules, ...).
- Against the 10-21 captures (tick and frame lines set aside):

  | verdict | unchanged, capture same | unchanged, capture differs | changed, capture differs | changed, capture same | not comparable |
  |---|---|---|---|---|---|
  | `vg_verdict` | 15 | **0** | 27 | 7 | 1 |
  | `vg_verdict_stable` | 16 | **0** | 27 | 6 | 1 |

- Stable unchanged by view type: all 7 drafting views, 6 of 7 legends (the
  seventh is new in B), 3 of 7 floor plans; no area plan, ceiling plan,
  elevation or section.
- The six still over-cautious: four elevations each rest on one or two
  members modified A -> B with identical pixels (8230062, 8230063, 8922372,
  shared across them); two RCPs on members deleted and re-created with new
  ids (11796xxx -> 12749xxx) and identical pixels.

## How the archives are kept unchanged

- **No transaction is opened** on any document: the probe only reads.
- Every document is closed with `Close(False)` (`opens.*.close`), and
  `IsModified` is recorded after the open and before the close.
- The **detached** opens open the archive itself, as the batch does: a
  detached document cannot save back to it.
- The `copy` control (not the default, refused for a cloud central) never
  opens the archive. It opens a byte copy (`shutil.copy2`) in
  `<probe folder>/_control_copy_<a|b>/`, which is deleted afterwards
  (`opens.*.copy_removed`).
- **Evidence:** every archive's size, mtime and sha256 are read before the
  first open and after the last close. `archives_changed` lists the fields
  that differ and `ARCHIVES_SUMMARY` says `ARCHIVES UNCHANGED` or
  `ARCHIVE CHANGED` in capitals. The probe does not try to fix a change.

`basic_file_info` (`BasicFileInfo.Extract`, read without opening) records
whether each archive is workshared, central or local, and its central path.

## The per-view part

Views: `IN[3]`, or a seeded sample of `sample_size` (50) drawn from A's
views across view types (`sample`: the population per type, the draw per
type, the seed, and `view_selection`). The population is the batch's own:
`batch_capture.select_views` over `collect_views`, read with its
`_view_type_name` (run `084944` used a reader of the probe's own that
matched no type and drew 0 views).

For each view, against the first `GetChangedElements` call that succeeded
(`per_view_against`, A detached first):

| signal | read |
|---|---|
| `members_created_or_modified` | B's membership (production `view_membership_ids`) ∩ (created ∪ modified) |
| `a_members_deleted` | A's membership, read while A was open, ∩ deleted |
| `view_itself` | the view's own id in created ∪ modified |
| `template` | the view's template id in created ∪ modified |
| `filters` | the view's and its template's filters in created ∪ modified (none, not unread, for a view whose `AreGraphicsOverridesAllowed()` is false) |
| `member_types` | the types B's members use (`GetTypeId`) in created ∪ modified |

`api_verdict` is `changed` when any of these fired, `unchanged` when none did
and all were read, and `unknown` when one could not be read. A signal that
was not read is `null`, never `false`. `membership_diff` (A's membership
against B's) is recorded beside it and is NOT part of the verdict: it is the
probe's own read, not the API's.

Known blind spots, INFERENCE until the analyzer measures them: a change
inside a linked model is not a change to the host's elements; an
object-style (category line weight or colour) change is a category setting,
not necessarily an element; a view-range or crop change may or may not
modify the view element. The analyzer's false-unchanged list is where these
would show.

## Timing

- `calls.*.call_ms`: the `GetChangedElements` call alone; `getters_ms` the
  three getters.
- `views[].membership_ms`, `member_types_ms`, `intersection_ms` (the set
  arithmetic alone), `view_total_ms`.
- `opens.*.open_ms`, `close_ms`, `copy_ms`.

## How to run (Greg)

1. Open Revit 2025 with a **small document that is not one of the two
   archives** open and active (as for the batch: Revit will not close the
   active document from the API). Dynamo 3.x, CPython3 Python node.
2. Paste `tests/dynamo/probe_change_detection.py` into the node, or load it
   with `exec(open(path).read())`.
3. Inputs:

| Input | Meaning | Example |
|---|---|---|
| `IN[0]` | output directory | `C:/Users/gmcdowell/Documents/_probes` |
| `IN[1]` | archive A, the EARLIER version | the V56 `2025-12-13` .rvt, full Desktop Connector path |
| `IN[2]` | archive B, the LATER version | the V57 `2025-12-20` .rvt |
| `IN[3]` | view ids, a list, or empty for the seeded sample of 50 | empty |
| `IN[4]` | options (Dictionary or JSON) | `{"repo_root": "C:/Users/gmcdowell/Documents/Revit_SSM_Exporter"}` |

   `IN[4]` keys (an unknown key is refused):

| Key | Meaning | Default |
|---|---|---|
| `repo_root` | the checkout, for `view_membership_ids` and `DEFAULT_VIEW_TYPES` | `REVIT_SSM_EXPORTER_ROOT`, then `~/Documents/Revit_SSM_Exporter` |
| `seed` | the sample's seed (and the negative control's GUID) | `20261022` |
| `sample_size` | views drawn when `IN[3]` is empty | `50` |
| `control` | `"reopen"`: A opened detached twice; `"copy"`: byte copies opened not detached (refused for a cloud central); `"none"` | `"reopen"` |
| `worksets` | `"all"` or `"last_viewed"`, as the batch's setting | `"all"` |
| `version_guid` | Q1b: read every element's VersionGuid in each detached open | `true` |
| `write_guid_maps` | also write each open's full `{id: VersionGuid}` map, `version_guids_<label>.json` (tens of MB each) | `false` |

4. Run. `OUT` is the JSON's path. If the probe could not start at all, `OUT`
   is a dict with `fatal`. Dismiss any dialog the auto-answers miss; every
   answered dialog is logged under `opens.*.events`.

**Expected run time:** three detached opens (A's took 51 s in run
`084944`, OBSERVED), four sha256 reads of the 1.1 GB archives, and about 100
view membership reads plus, per view and per open, its template, filters and
member types; then three whole-document VersionGuid reads (not yet measured:
`opens.*.version_guids.ms` is the measurement). Roughly 15-30 minutes in all
(INFERENCE; the three opens took 48-55 s each in run `095313`). `progress_<stamp>.json` is rewritten after each phase, so a
crash still shows how far it got (`finalized: false`; it is never the record).

### Then, offline (Greg, on the machine with the runs)

Find the 2026-10-21 batch's run directories for A and B (the
`<as_of>__<run_id>` folders under the export root), then:

```bash
python tools/analyze_change_detection_probe.py <IN[0]>/change_detection_<stamp> \
    --run-a <export root>/2025-12-13__<run id> \
    --run-b <export root>/2025-12-20__<run id>
```

It writes `change_detection_analysis.json` beside the probe JSON and prints
the table.

## Files to return

The whole folder `<IN[0]>/change_detection_<stamp>/`:

- `probe_change_detection_<stamp>.json` (the record, written last)
- `changed_ids_<label>.json`, one per successful call (Q1b: only B's own-GUID control)
- `progress_<stamp>.json`
- `change_detection_analysis.json`, after the offline step

and the two run directory names used for `--run-a` / `--run-b`.

## Outputs

`probe_change_detection_<stamp>.json`:

- `reflection`: Revit version and build, the API assembly, every overload
  found, the return types' members, `q1_can_run`
- `production`: where `vop_interwoven` was loaded from
- `archives_before`, `archives_after`, `archives_changed`, `ARCHIVES_SUMMARY`
- `basic_file_info` per archive
- `opens.<a_detached | a_detached_reopen | b_detached>` (and, under
  `control: "copy"`, `a_copy_not_detached` / `b_copy_not_detached` or
  `copy_control_refused`):
  how it was opened (detach, worksets, audit), events (warnings deleted,
  dialogs answered), title, `is_detached`, `is_workshared`, `version`,
  `IsModified` after open and before close, timings
- `versions`: GUID and saves per open
- `calls.<b open>__vs__<against>`: `state`, the exception verbatim
  (`exception_type`, `exception_message`, `dotnet_type`) or the three counts,
  `ids_file`, `created_or_modified_by_category`, `by_change_type`, timings
- `sample`, `per_view_against`, `views[]` (signals, `api_verdict`,
  `membership_diff`, timings)
- `exceptions`: every exception with its stage and traceback

`change_detection_analysis.json`:

- `table`: `api_verdict` (changed / unchanged / unknown / absent_in_b)
  against capture `differs` / `same` / `not_comparable`
- `false_unchanged`: each view the API called unchanged whose capture
  differs, with the counts that differ and `cause_inference`
  (`view_setting`, `element`, `link`, `type_or_graphics`, `other`; INFERENCE
  from the captures)
- `api_changed_capture_same`
- `views[]`: per view, the decoded-pixel comparison of both passes (ticks
  excluded; linked pixels compared by category name), the frame fields that
  differ, and the bbox-map additions, removals and moves

"Differs" is any count above zero. No threshold is applied: the counts are
in the record for another rule to be applied later.

## What is reimplemented, not imported

- The batch's open (`_open_options`, the FailuresProcessing handler, the
  dialog answers): a pyRevit script cannot be imported. Copied from
  `pyrevit/.../BatchCapture.pushbutton/script.py`; a change there must be
  copied here.
- `view_membership_ids` and `DEFAULT_VIEW_TYPES` are imported from the
  checkout (fresh: cached `vop_interwoven` modules are purged first).

## Results

*(Greg: fill in after the run.)*
