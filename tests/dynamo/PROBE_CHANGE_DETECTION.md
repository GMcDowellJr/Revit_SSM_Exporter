# Change-detection probe (Q1)

`probe_change_detection.py` answers one question with measurements, before
anything is designed: **can Revit tell us cheaply which elements changed
between two captured model versions, so a later run can skip the views that
did not change?** The offline half is `tools/analyze_change_detection_probe.py`,
which checks the API's answer against captures already made.

**UNVERIFIED in Revit.** Only the pure parts are tested offline
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

4. Run. `OUT` is the JSON's path. If the probe could not start at all, `OUT`
   is a dict with `fatal`. Dismiss any dialog the auto-answers miss; every
   answered dialog is logged under `opens.*.events`.

**Expected run time:** three detached opens (A's took 51 s in run
`084944`, OBSERVED), four sha256 reads of the 1.1 GB archives, and about 100
view membership reads (not yet measured); roughly 10-25 minutes in all
(INFERENCE). `progress_<stamp>.json` is rewritten after each phase, so a
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
- `changed_ids_<label>.json`, one per successful call
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
