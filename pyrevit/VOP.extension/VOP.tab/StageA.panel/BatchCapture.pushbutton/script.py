#! python3
"""
Stage A Batch Capture (archives via Desktop Connector) -- pyRevit push-button.

Opens each archive of a capture plan, runs the Stage A capture (a NEW run)
or the RECAPTURE of an existing run against it, closes it, and goes on.
The plan is written beforehand by

    python tools/plan_capture_batch.py <manifest.csv> --output-root <Exports> \
        [--recapture-lists recapture_lists.json] [--mode both|capture|recapture]

and reviewed/edited before this button opens anything. Resumable: a job
whose outcome is already in the run's files is skipped
(vop_interwoven.batch_capture.pending_jobs). Run with a small document OPEN
and active that is not one of the plan's archives: Revit will not close the
active document from the API, so it must never be an archive.

Settings (batch_settings.json beside this script):
    plan_json          path to capture_plan.json           (required)
    repo_root          the Revit_SSM_Exporter checkout; blank = derived
                       from this script's location (pyrevit/ sits in it)
    limit              max jobs this run, blank = all pending
    batch_size         views per streaming batch, blank = one batch
    export_view_raster raw Revit view PNGs beside the captures (false)
    worksets           "all" (default) or "last_viewed"
    record_fingerprints  hash-and-skip phase 1: write each capture run's view
                       fingerprints (true, default); a plan made with
                       --mode fingerprint backfills existing runs instead

Every file is opened DETACHED (DetachAndPreserveWorksets) with the chosen
workset configuration, read-only in effect; dialogs during the open are
auto-dismissed and their ids logged (as the Fingerprint extension does).
How each document was opened is recorded in its run's run_meta.json
(``opened_from``) and in the batch log the loop writes under the output
root after every job.
"""

import json
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Settings and repo root
# ---------------------------------------------------------------------------

try:
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _SCRIPT_DIR = os.getcwd()
_SETTINGS_PATH = os.path.join(_SCRIPT_DIR, "batch_settings.json")
_DEFAULTS = {"plan_json": "", "repo_root": "", "limit": "", "batch_size": "",
             "record_fingerprints": True,
             "export_view_raster": False, "worksets": "all"}


def _load_settings():
    """``(settings, error)``: the settings file merged over the defaults, or
    the defaults with the reason it could not be read. A file that exists
    but will not parse is an ERROR, never silently the defaults: that path
    wrote the first-run template over Greg's edited file (2026-10-13). The
    file is read as utf-8-sig, so a byte-order mark from Notepad or
    PowerShell is not a parse error."""
    out = dict(_DEFAULTS)
    if not os.path.isfile(_SETTINGS_PATH):
        return out, None
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8-sig") as handle:
            settings = json.load(handle)
    except (IOError, OSError, ValueError) as ex:
        return out, "{0}: {1}".format(type(ex).__name__, ex)
    if not isinstance(settings, dict):
        return out, "the file holds a {0}, not a JSON object".format(type(settings).__name__)
    out.update(settings)
    return out, None


def _derived_repo_root():
    # pyrevit/VOP.extension/VOP.tab/StageA.panel/BatchCapture.pushbutton/script.py
    root = _SCRIPT_DIR
    for _ in range(5):
        root = os.path.dirname(root)
    return root


_settings, _settings_error = _load_settings()
REPO_ROOT = _settings.get("repo_root") or _derived_repo_root()
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# ---------------------------------------------------------------------------
# Revit / pyRevit
# ---------------------------------------------------------------------------

import clr  # noqa: E402
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.DB import (  # noqa: E402
    DetachFromCentralOption, ModelPathUtils, OpenOptions, WorksetConfiguration,
    WorksetConfigurationOption,
)
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommandLinkId, TaskDialogResult  # noqa: E402

ui_app = __revit__  # noqa: F821

# ---------------------------------------------------------------------------
# Dialog suppressor (the Fingerprint extension's). Every dialog is logged
# with its time, the job it came up in and the result it was answered with
# (batch_capture.DialogLog, persisted in the batch log after every job).
# ---------------------------------------------------------------------------

_dialog_log = []    # [DialogLog], set in main() once the package is importable
_events = []        # every open event, in order: the dialog_ids_<clock>.log lines

# Known dialogs during an open, answered by id. Codes: 1 = IDOK, 8 = Close
# (TaskDialogResult.Close), 1002 = CommandLink2. Verified by Greg's manual open
# of V50 (2026-10-20): DocWarnDialog = OK / Cancel / Delete Room(s);
# Elements_Lost = Close only; Unresolved References CommandLink2 = "Ignore and
# continue opening the project".
_DIALOG_ANSWERS = {
    "Dialog_Revit_DocWarnDialog": (1,),
    "TaskDialog_Elements_Lost_On_Import": (8, 1),
    "TaskDialog_Unresolved_References": (1002,),
}
_LEGACY_ORDER = (1002, 1001, 1)


def _note(text):
    """One line per event, in order (not de-duplicated), stamped with the time
    and the job running: the dialog log and the pyRevit output window both
    show the sequence of the open."""
    job_key = _dialog_log[0].job_key if _dialog_log else None
    _events.append("{0}\t{1}\t{2}".format(
        datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), job_key or "", text))
    print("  [open] {0}".format(text))


def _on_dialog_showing(sender, args):
    dialog_id = ""
    try:
        dialog_id = str(args.DialogId) if args.DialogId else ""
    except Exception as ex:  # noqa: BLE001 -- the id is for the log only
        dialog_id = "(unreadable: {0})".format(ex)
    message = getattr(args, "Message", None)
    codes = _DIALOG_ANSWERS.get(dialog_id)
    attempts = []
    answered = None
    for result_code in (codes or _LEGACY_ORDER):
        try:
            accepted = args.OverrideResult(result_code)
        except Exception as ex:  # noqa: BLE001 -- recorded; the next code is tried
            attempts.append("{0}:{1}".format(result_code, type(ex).__name__))
            continue
        attempts.append("{0}:{1}".format(result_code, accepted))
        if accepted:
            answered = result_code
            break
    # The batch log's record (C4): time, job key, id, the code that took.
    if _dialog_log:
        _dialog_log[0].record(dialog_id, answered)
    _note("dialog {0}{1} ({2}) tried {3}{4}".format(
        dialog_id, "" if codes else " [unmapped]", type(args).__name__,
        ", ".join(attempts),
        " | {0}".format(str(message)[:120].replace("\n", " ")) if message else ""))


# ---------------------------------------------------------------------------
# Opening and closing
# ---------------------------------------------------------------------------

def _open_options():
    opts = OpenOptions()
    opts.DetachFromCentralOption = DetachFromCentralOption.DetachAndPreserveWorksets
    opts.Audit = False
    if str(_settings.get("worksets") or "all").lower() == "last_viewed":
        option, name = WorksetConfigurationOption.OpenLastViewed, "last_viewed"
    else:
        option, name = WorksetConfigurationOption.OpenAllWorksets, "all"
    opts.SetOpenWorksetsConfiguration(WorksetConfiguration(option))
    return opts, {"detach": "DetachAndPreserveWorksets", "worksets": name, "audit": False}


from Autodesk.Revit.DB import FailureProcessingResult, FailureSeverity  # noqa: E402


def _on_failures_processing(sender, args):
    """Subscribed only while an archive opens. When everything posted is a
    warning (V50: 407, "Multiple Rooms in the same enclosed region"), the
    warnings are deleted so Revit shows no warnings dialog. Errors are left
    to Revit. The document is closed unsaved, so nothing is written back."""
    try:
        accessor = args.GetFailuresAccessor()
        messages = list(accessor.GetFailureMessages())
        warnings = [m for m in messages if m.GetSeverity() == FailureSeverity.Warning]
        if messages and len(warnings) == len(messages):
            accessor.DeleteAllWarnings()
            args.SetProcessingResult(FailureProcessingResult.Continue)
            _note("failures_processing: {0} warnings deleted".format(len(warnings)))
        elif messages:
            _note("failures_processing: {0} messages, {1} warnings, left to Revit".format(
                len(messages), len(warnings)))
    except Exception as ex:  # noqa: BLE001 -- recorded; Revit's own handling continues
        _note("failures_processing: error {0}: {1}".format(type(ex).__name__, ex))


def open_doc(job):
    path = job["full_path"]
    if not os.path.isfile(path):
        raise IOError("not on disk (not synced by Desktop Connector?): {0}".format(path))
    opts, how = _open_options()
    how["open_warnings"] = "deleted_in_failures_processing"
    model_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
    _note("open: {0}".format(os.path.basename(path)))
    app = ui_app.Application
    app.FailuresProcessing += _on_failures_processing
    try:
        doc = app.OpenDocumentFile(model_path, opts)
    finally:
        app.FailuresProcessing -= _on_failures_processing
    if doc is None:
        raise RuntimeError("OpenDocumentFile returned None for {0}".format(path))
    _note("opened: title={0!r}".format(doc.Title))
    return doc, how


def close_doc(doc):
    doc.Close(False)


# ---------------------------------------------------------------------------
# The active document. Revit will not close the ACTIVE document from the API,
# and with nothing open the first archive the API opened became the active
# one (batch 2026-10-22: V50 was never closed and stayed resident, about
# 2.6 GB, while V51 was captured). The batch starts with a document the user
# opened instead; every archive is then a background document the loop
# closes as soon as its job ends (run_batch close_after_capture).
# ---------------------------------------------------------------------------

def _active_document():
    """``(title, path)`` of the active document, or ``(None, None)``."""
    uidoc = ui_app.ActiveUIDocument
    if uidoc is None:
        return None, None
    return uidoc.Document.Title, uidoc.Document.PathName


# ---------------------------------------------------------------------------
# The two jobs
# ---------------------------------------------------------------------------

def _config():
    from vop_interwoven.config import Config
    cfg = Config()
    cfg.debug_json_detail = "summary"
    cfg.enable_color_id_buffer_stage_a = True
    cfg.color_id_buffer_registered_capture = True
    return cfg


def _int_or_none(text):
    text = str(text or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def capture(doc, job):
    from vop_interwoven.batch_capture import collect_views, select_views, stamp_run_meta
    from vop_interwoven.capture_run import run_capture
    views, selection = select_views(collect_views(doc), job.get("view_types") or ())
    view_ids = [v.Id.Value if hasattr(v.Id, "Value") else v.Id.IntegerValue for v in views]
    print("  Views selected: {0} ({1})".format(len(view_ids), selection["kept"]))
    if not view_ids:
        raise RuntimeError("no view of the selected types in {0}".format(doc.Title))
    result, run_dir, run_id = run_capture(
        doc, view_ids, _config(), job["output_dir"],
        batch_size=_int_or_none(_settings.get("batch_size")),
        date_override=job.get("date_override"),
        export_view_raster=bool(_settings.get("export_view_raster")),
        record_fingerprints=_flag(_settings.get("record_fingerprints"), True))
    err = stamp_run_meta(run_dir, "view_selection", selection)
    return {"run_dirs": [run_dir], "run_id": run_id,
            "views_processed": result.get("views_processed"),
            "views_failed": result.get("views_failed"),
            "view_selection_stamp_failed": err}


def _flag(value, default):
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes")


def fingerprint(doc, job):
    """Hash-and-skip backfill (docs/DESIGN_CHANGE_SKIP.md): write the view
    fingerprints of each existing run captured from this archive."""
    from vop_interwoven.view_fingerprint import fingerprint_existing_run
    done, errors = [], {}
    for run_dir in job["fingerprint_runs"]:
        try:
            out = fingerprint_existing_run(doc, run_dir)
            done.append(run_dir)
            print("  fingerprinted {0}: {1}".format(run_dir, out["summary"]))
        except Exception as ex:  # noqa: BLE001 -- recorded in the job's result
            errors[run_dir] = "{0}: {1}".format(type(ex).__name__, ex)
    if not done:
        raise RuntimeError("no run fingerprinted: {0}".format(errors))
    return {"run_dirs": done, "errors": errors}


def recapture(doc, job):
    from vop_interwoven.recapture import run_recapture
    lists = {"runs": [{"run_dir": t["run_dir"], "run_id": t.get("run_id"),
                       "recapture": t["view_ids"], "reason": t.get("reason")}
                      for t in job["recapture"]]}
    out = run_recapture(doc, lists, _config(), batch_size=_int_or_none(_settings.get("batch_size")))
    return {"run_dirs": [r["run_dir"] for r in out["runs"] if not r.get("skipped")],
            "refused": out["refused"], "views_processed": out["views_processed"],
            "views_failed": out["views_failed"],
            "runs": [dict((k, v) for k, v in r.items()) for r in out["runs"]]}


# ---------------------------------------------------------------------------
# Main. A function, so an early exit is a return: pyRevit's CPython engine
# reports SystemExit as a script error ("CPython Traceback: 0").
# ---------------------------------------------------------------------------

def main():
    if _settings_error:
        # Shown, not worked around: the file is left exactly as it is.
        TaskDialog.Show("VOP Batch Capture -- settings not read",
                        "{0}\n\ncould not be read:\n{1}\n\nFix the file (plain UTF-8 JSON) "
                        "and press the button again. Nothing was written.".format(
                            _SETTINGS_PATH, _settings_error))
        return
    if not _settings.get("plan_json"):
        if not os.path.isfile(_SETTINGS_PATH):
            with open(_SETTINGS_PATH, "w", encoding="utf-8") as handle:
                json.dump(_DEFAULTS, handle, indent=2)
            written = "A template settings file has been written:"
        else:
            written = "plan_json is blank in:"
        TaskDialog.Show("VOP Batch Capture -- first run",
                        "{0}\n\n{1}\n\nFill in plan_json (and repo_root if pyrevit/ is not "
                        "inside the checkout), then press the button again.".format(
                            written, _SETTINGS_PATH))
        return

    if not os.path.isfile(os.path.join(REPO_ROOT, "vop_interwoven", "batch_capture.py")):
        # The extension copied out of the checkout derives the wrong root
        # (Greg, 2026-10-13: ImportError at the import below).
        TaskDialog.Show("VOP Batch Capture -- repo_root",
                        "vop_interwoven was not found under\n\n{0}\n\nSet repo_root in\n{1}\n"
                        "to the Revit_SSM_Exporter checkout (the folder holding vop_interwoven). "
                        "It is derived from this script's location only when pyrevit/ sits "
                        "inside the checkout.".format(REPO_ROOT, _SETTINGS_PATH))
        return
    from vop_interwoven.batch_capture import (  # noqa: E402
        DialogLog, active_document_refusal, load_plan, pending_jobs, run_batch)

    plan = load_plan(_settings["plan_json"])
    pending, done = pending_jobs(plan, limit=_int_or_none(_settings.get("limit")))
    active_title, active_path = _active_document()
    refusal = active_document_refusal(pending, active_title, active_path)
    if refusal:
        TaskDialog.Show("VOP Batch Capture -- open a document first",
                        "The batch did not start: {0}".format(refusal))
        return
    _confirm = TaskDialog("VOP Batch Capture -- confirm")
    _confirm.MainInstruction = "Ready to run"
    _confirm.MainContent = (
        "Plan:        {0}\nMode:        {1}\nOutput root: {2}\nRepo root:   {3}\n\n"
        "Jobs pending: {4}   already done: {5}\n"
        "(first: {6})\nActive document: {8} (stays open)\n\n"
        "Open each archive detached, {7} worksets; proceed?".format(
            _settings["plan_json"], plan.get("mode"), plan.get("output_root"), REPO_ROOT,
            len(pending), len(done), pending[0]["display_name"] if pending else "-",
            _settings.get("worksets") or "all", active_title))
    _confirm.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "Run")
    _confirm.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "Open settings in Notepad, then cancel")
    _result = _confirm.Show()
    if _result == TaskDialogResult.CommandLink2:
        import subprocess
        subprocess.Popen(["notepad.exe", _SETTINGS_PATH])
        return
    if _result != TaskDialogResult.CommandLink1:
        return

    _dialog_log[:] = [DialogLog()]
    ui_app.DialogBoxShowing += _on_dialog_showing
    try:
        session = {"active_document": active_title, "active_document_path": active_path}
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        try:
            record = run_batch(plan, open_doc, close_doc, capture, recapture,
                               limit=_int_or_none(_settings.get("limit")), log=print,
                               dialog_log=_dialog_log[0],
                               close_after_capture=True, session=session,
                               fingerprint=fingerprint)
        finally:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    finally:
        ui_app.DialogBoxShowing -= _on_dialog_showing

    # One line per open event: time, job key, event (dialogs are also in the
    # batch log, with the code that took).
    if _events and plan.get("output_root"):
        try:
            with open(os.path.join(plan["output_root"], "dialog_ids_{0}.log".format(
                    datetime.now().strftime("%Y%m%dT%H%M%S"))), "w", encoding="utf-8") as handle:
                handle.write("\n".join(_events) + "\n")
        except (IOError, OSError) as ex:
            print("  dialog id log not written: {0}".format(ex))

    summary = ["Jobs OK: {0}   failed: {1}   already done: {2}".format(
        record["ok"], record["failed"], len(record["already_done"])),
        "Log: {0}".format(record.get("log_path"))]
    for entry in record["jobs"]:
        if entry["status"] != "ok":
            summary.append("  FAILED {0}: {1}".format(entry["key"], entry.get("error")))
        for refused in (entry.get("result") or {}).get("refused") or []:
            summary.append("  REFUSED {0}: {1}".format(refused.get("run_id"), refused.get("reason")))
    if record.get("last_document_closed") is not True:
        summary.append("Last document left open: {0}".format(record.get("last_document_closed")))
    if record.get("documents_left_open"):
        summary.append("Still open (close refused): {0}".format(
            ", ".join(str(t) for t in record["documents_left_open"])))
    summary.append("")
    summary.append("Next: register_stage_a_annotation.py, stage_a_grid.py, stage_a_kinds.py and "
                   "stage_a_grid_rollup.py over each run written.")
    TaskDialog.Show("VOP Batch Capture -- done", "\n".join(summary))


main()
