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
(vop_interwoven.batch_capture.pending_jobs). Run with no document open.

Settings (batch_settings.json beside this script):
    plan_json          path to capture_plan.json           (required)
    repo_root          the Revit_SSM_Exporter checkout; blank = derived
                       from this script's location (pyrevit/ sits in it)
    limit              max jobs this run, blank = all pending
    batch_size         views per streaming batch, blank = one batch
    export_view_raster raw Revit view PNGs beside the captures (false)
    worksets           "all" (default) or "last_viewed"

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
             "export_view_raster": False, "worksets": "all"}


def _load_settings():
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as handle:
            settings = json.load(handle)
    except (IOError, OSError, ValueError):
        settings = {}
    out = dict(_DEFAULTS)
    out.update(settings or {})
    return out


def _derived_repo_root():
    # pyrevit/VOP.extension/VOP.tab/StageA.panel/BatchCapture.pushbutton/script.py
    root = _SCRIPT_DIR
    for _ in range(5):
        root = os.path.dirname(root)
    return root


_settings = _load_settings()
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
# Dialog suppressor (the Fingerprint extension's; every id is logged)
# ---------------------------------------------------------------------------

_seen_dialog_ids = []


def _on_dialog_showing(sender, args):
    dialog_id = ""
    try:
        dialog_id = str(args.DialogId) if args.DialogId else ""
    except Exception as ex:  # noqa: BLE001 -- the id is for the log only
        dialog_id = "(unreadable: {0})".format(ex)
    if dialog_id and dialog_id not in _seen_dialog_ids:
        _seen_dialog_ids.append(dialog_id)
    for result_code in (1002, 1001, 1):
        try:
            if args.OverrideResult(result_code):
                break
        except Exception:  # noqa: BLE001 -- the next code is tried
            continue


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


def open_doc(job):
    path = job["full_path"]
    if not os.path.isfile(path):
        raise IOError("not on disk (not synced by Desktop Connector?): {0}".format(path))
    opts, how = _open_options()
    model_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
    doc = ui_app.Application.OpenDocumentFile(model_path, opts)
    if doc is None:
        raise RuntimeError("OpenDocumentFile returned None for {0}".format(path))
    print("  Opened: title={0!r}".format(doc.Title))
    return doc, how


def close_doc(doc):
    doc.Close(False)


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
        export_view_raster=bool(_settings.get("export_view_raster")))
    err = stamp_run_meta(run_dir, "view_selection", selection)
    return {"run_dirs": [run_dir], "run_id": run_id,
            "views_processed": result.get("views_processed"),
            "views_failed": result.get("views_failed"),
            "view_selection_stamp_failed": err}


def recapture(doc, job):
    from vop_interwoven.recapture import run_recapture
    lists = {"runs": [{"run_dir": t["run_dir"], "run_id": t.get("run_id"),
                       "recapture": t["view_ids"]} for t in job["recapture"]]}
    out = run_recapture(doc, lists, _config(), batch_size=_int_or_none(_settings.get("batch_size")))
    return {"run_dirs": [r["run_dir"] for r in out["runs"] if not r.get("skipped")],
            "refused": out["refused"], "views_processed": out["views_processed"],
            "views_failed": out["views_failed"],
            "runs": [dict((k, v) for k, v in r.items()) for r in out["runs"]]}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if not _settings.get("plan_json"):
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as handle:
        json.dump(_DEFAULTS, handle, indent=2)
    TaskDialog.Show("VOP Batch Capture -- first run",
                    "Fill in plan_json (and repo_root if pyrevit/ is not inside the "
                    "checkout) in:\n\n{0}\n\nthen press the button again.".format(_SETTINGS_PATH))
    raise SystemExit(0)

from vop_interwoven.batch_capture import load_plan, pending_jobs, run_batch  # noqa: E402

plan = load_plan(_settings["plan_json"])
pending, done = pending_jobs(plan, limit=_int_or_none(_settings.get("limit")))
_confirm = TaskDialog("VOP Batch Capture -- confirm")
_confirm.MainInstruction = "Ready to run"
_confirm.MainContent = (
    "Plan:        {0}\nMode:        {1}\nOutput root: {2}\nRepo root:   {3}\n\n"
    "Jobs pending: {4}   already done: {5}\n"
    "(first: {6})\n\nOpen each archive detached, {7} worksets; proceed?".format(
        _settings["plan_json"], plan.get("mode"), plan.get("output_root"), REPO_ROOT,
        len(pending), len(done), pending[0]["display_name"] if pending else "-",
        _settings.get("worksets") or "all"))
_confirm.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "Run")
_confirm.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "Open settings in Notepad, then cancel")
_result = _confirm.Show()
if _result == TaskDialogResult.CommandLink2:
    import subprocess
    subprocess.Popen(["notepad.exe", _SETTINGS_PATH])
    raise SystemExit(0)
if _result != TaskDialogResult.CommandLink1:
    raise SystemExit(0)

ui_app.DialogBoxShowing += _on_dialog_showing
try:
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    try:
        record = run_batch(plan, open_doc, close_doc, capture, recapture,
                           limit=_int_or_none(_settings.get("limit")), log=print)
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
finally:
    ui_app.DialogBoxShowing -= _on_dialog_showing

if _seen_dialog_ids and plan.get("output_root"):
    try:
        with open(os.path.join(plan["output_root"], "dialog_ids_{0}.log".format(
                datetime.now().strftime("%Y%m%dT%H%M%S"))), "w", encoding="utf-8") as handle:
            handle.write("\n".join(_seen_dialog_ids) + "\n")
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
summary.append("")
summary.append("Next: register_stage_a_annotation.py, stage_a_grid.py, stage_a_kinds.py and "
               "stage_a_grid_rollup.py over each run written.")
TaskDialog.Show("VOP Batch Capture -- done", "\n".join(summary))
