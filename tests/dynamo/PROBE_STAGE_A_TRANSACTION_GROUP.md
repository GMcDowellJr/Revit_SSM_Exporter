# Stage A TransactionGroup Export Probe

## Purpose

`probe_stage_a_transaction_group_export.py` tests the architectural question for Stage A: can a Revit view receive temporary view-scoped graphics in a committed child `Transaction`, be exported through `Document.ExportImage()` while the enclosing `TransactionGroup` is still open, and then return to its captured source state by rolling back that group?

The probe does **not** change production Stage A code, duplicate the view, manually restore graphics, clear overrides with a blank `OverrideGraphicSettings`, or create project-wide graphics resources.

## Prerequisites

* Revit 2025.
* Dynamo 3.3.
* A CPython3 Python Script node.
* The repository path available to Python if you load the script from disk.
* A non-production model or safe model copy.
* A model-capable orthographic view that can be printed/exported.
* One or more visible model elements from the same document as the target view.
* A writable output directory.

## Dynamo wiring

Use these inputs:

```text
IN[0] = target Revit view
IN[1] = one test element or a list of test elements
IN[2] = output directory
IN[3] = inject failure after TIFF export: bool, default False
```

The script accepts raw Revit API objects and common Dynamo-wrapped Revit objects. It rejects invalid inputs instead of substituting the active view or arbitrary elements. For Revit 2025 compatibility, ElementId snapshots and validation read `ElementId.Value` first and only fall back to `IntegerValue` for older APIs.

## Recommended test element

Prefer an element that is:

* Visible and sufficiently large in the target view.
* Not fully occluded.
* Not in a linked model for the first run.
* Easy to identify in the TIFF.

## Manual-override test

For the strongest restoration evidence:

1. Through Revit's UI, apply an obvious manual per-element view override to one selected element before running the probe.
2. Run the probe.
3. Confirm the original manual override remains after rollback.
4. Compare the JSON before/after `OverrideGraphicSettings` snapshots, including the Revit 2025 `Transparency` property captured as `surface_transparency`.

Do not have the script create a persistent baseline override.

## Required runs

Run the same view and element selection twice:

1. `IN[3] = False`: normal successful export.
2. `IN[3] = True`: controlled failure immediately after TIFF export.

Both runs must show successful transaction-group rollback and equal captured state.

## Outputs

Reports are written under:

```text
<output directory>/transaction_group_probe/
```

Expected files per run:

```text
<safe_view>_<view_id>.transaction_group_probe.tiff
<safe_view>_<view_id>.transaction_group_probe.json
```

The Dynamo `OUT` dictionary includes the conclusion, TIFF path, JSON report path, rollback status, captured-state equality, state differences, expected temporary colors, expected color pixel counts, missing expected colors, child transaction commit status, optional image-inspection results, exceptions, and the required second-run instruction.

## Manual verification checklist

After each run:

* Open the TIFF and confirm the temporary colors appear.
* Confirm the source view still shows the original graphics.
* Confirm any pre-existing manual override remains.
* Confirm crop, template, filters, and display style appear unchanged.
* Confirm no temporary view, filter, material, Object Style, or category definition was created.
* Review the JSON `state_differences` list.
* Check Revit's undo list manually and report whether a probe transaction appears, while recognizing that this is manual evidence rather than API-verified evidence.

## Interpretation

### PASS

A passing run means:

* The transaction group started.
* `Transaction.Commit()` returned `TransactionStatus.Committed`; any other status skips export and prevents PASS.
* TIFF export succeeded with no child transaction open.
* Transaction-group rollback succeeded.
* The captured before/after state is equal.
* No unexpected exception occurred.
* If Pillow image inspection runs, at least one expected temporary color is detected; `not_detected` prevents PASS.
* For the failure-injection run, the controlled failure triggered and rollback still succeeded.

### FAIL

A failing run means one or more required structural checks failed, `Transaction.Commit()` returned a non-committed status, rollback failed, export failed, captured state differs, Pillow inspection found zero pixels for every expected temporary color, or an unexpected exception occurred. If rollback fails, inspect the reported target view immediately.

### INCONCLUSIVE

An inconclusive run means the probe produced partial useful evidence but not enough to support the transaction-group rollback strategy. For example, the export and rollback may succeed while another unexpected condition prevents a clean pass.

## Limits of this probe

A passing result supports replacing explicit Stage A manual restoration with transaction-group rollback. It does not establish crop alignment, filter semantics, linework-mode behavior, linked-element behavior, annotation behavior, undo-stack preservation, image decoding, or Stage B integration.

The optional image inspection depends on Pillow being available in the Dynamo CPython3 environment. If unavailable, the JSON records `temporary_colors_visible_in_export = "requires_manual_review"`; manual TIFF review is still required. If Pillow is available and records `temporary_colors_visible_in_export = "not_detected"`, the probe does not PASS because the export did not demonstrate that the temporary graphics rendered.
