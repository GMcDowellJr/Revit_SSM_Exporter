"""Batch capture across documents: open each archive from a manifest, run a
Stage A capture (or the recapture of an existing run) against it, close it,
and go on -- the Fingerprint extension's loop, for the exporter.

Why (2026-10-12). 43 runs may need their rotated-crop views recaptured
(tools/find_recapture_views.py -> recapture_lists.json), and every one is a
different archive on ACC, mirrored locally by Desktop Connector. Opening
each by hand is the bottleneck; the same loop then captures NEW archives
without opening each one.

Shape. Everything that decides is here and pure; what touches Revit is
injected (``open_doc``, ``close_doc``, ``capture``, ``recapture``), so the
loop is tested without Revit and the pyRevit button
(pyrevit/VOP.extension/.../BatchCapture.pushbutton/script.py) is thin.

* ``load_manifest(csv)``: the Fingerprint manifest (``include``,
  ``relative_path``, ``full_path``, ``project_folder``, ``filename``).
* ``build_plan(...)``: which file gets which job -- a RECAPTURE job for
  every run of ``recapture_lists.json`` whose recorded ``doc_title`` is the
  file's stem, a CAPTURE job for a file no finalized run under the output
  root was captured from (unless ``skip_captured`` is off). Written as
  ``capture_plan.json`` by tools/plan_capture_batch.py, for review and
  editing BEFORE Revit opens anything: the plan is the input, the manifest
  and lists are not re-read inside Revit.
* ``pending_jobs(plan)``: resume -- a job whose outcome is already in the
  run's files is skipped (a finalized run_meta captured from the file; a
  ``recaptures`` entry covering the ids).
* ``run_batch(...)``: the loop. Each job's outcome is recorded in a durable
  ``batch_capture_<clock>.json`` under the output root after EVERY job (an
  interrupted batch leaves a record of what it did), and run_meta.json of
  each run the job wrote gains ``opened_from`` (how the document was opened:
  the manifest path, detached or not, which worksets, the title Revit gave
  it) -- a detached, all-worksets open is not the same environment as the
  interactive one the earlier runs had.
* ``select_views(views, view_types)``: the capture job's views -- every
  non-template view whose ViewType is in ``view_types``
  (DEFAULT_VIEW_TYPES: the 06-21 run's population minus ThreeD, which the
  pipeline rejects by capability). Recorded in run_meta as
  ``view_selection``.

Document title. A recapture refuses a document whose title is not the
run's recorded ``doc_title``; ``title_matches`` also accepts the recorded
title with Revit's ``_detached`` suffix, since the batch opens detached.
Whether Revit titles a detached document that way is NOT confirmed from
documentation (2026-10-12); ``opened_from.title`` records what it did, and
a refused run names both titles.
"""

import csv
import json
import os
import re

from . import output_layout
from .run_meta import clock_stamp, write_run_meta

DEFAULT_VIEW_TYPES = (
    "FloorPlan", "CeilingPlan", "AreaPlan", "EngineeringPlan", "Section",
    "Elevation", "Detail", "DraftingView", "Legend",
)
DETACHED_SUFFIX = "_detached"
BATCH_LOG_PREFIX = "batch_capture_"
PLAN_SCHEMA = "vop.capture_plan.v1"
BATCH_LOG_SCHEMA = "vop.batch_capture_log.v1"
_DATE_SUFFIX = re.compile(r"(?:^|[_\- ])(\d{4}-\d{2}-\d{2})$")


class BatchPlanError(ValueError):
    """The plan cannot be built or run as asked; nothing is written."""


# --- manifest ------------------------------------------------------------------------

def load_manifest(csv_path, limit=None):
    """The Fingerprint manifest's included rows, in file order. Each entry:
    relative_path (the key), full_path (what Revit opens), filename,
    project_folder, display_name, stem (filename without extension; the
    document title Revit gives the file)."""
    entries = []
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in ("include", "relative_path", "full_path")
                   if c not in (reader.fieldnames or [])]
        if missing:
            raise BatchPlanError("{0} lacks manifest column(s) {1}".format(csv_path, missing))
        for row in reader:
            if (row.get("include") or "").strip() != "1":
                continue
            relative_path = (row.get("relative_path") or "").strip()
            full_path = (row.get("full_path") or "").strip()
            if not relative_path or not full_path:
                continue
            project = (row.get("project_folder") or "").strip()
            fname = (row.get("filename") or os.path.basename(full_path)).strip()
            stem = os.path.splitext(fname)[0]
            entries.append({
                "relative_path": relative_path, "full_path": full_path,
                "filename": fname, "project_folder": project, "stem": stem,
                "display_name": "{0}/{1}".format(project, fname) if project else fname,
            })
    if limit:
        entries = entries[:int(limit)]
    return entries


def archive_date(stem):
    """The as-of date an archive's name carries (``..._2025-06-21``), or
    None: the run's ``date`` is then the capture day, and the plan says so."""
    match = _DATE_SUFFIX.search(stem or "")
    return match.group(1) if match else None


def title_matches(recorded, opened):
    """The open document is the one a run was captured from: the same title,
    or that title with Revit's detached suffix. The plan calls it with the
    file's stem as ``recorded`` and a run's doc_title as ``opened``: a run the
    batch made carries the suffix, the stem never does."""
    if recorded is None or opened is None:
        return False
    recorded, opened = str(recorded), str(opened)
    return opened == recorded or opened == recorded + DETACHED_SUFFIX


# --- the runs already there ------------------------------------------------------------

def _load_json(path):
    with open(path) as handle:
        return json.load(handle)


def _value(record):
    return record.get("value") if isinstance(record, dict) and record.get(
        "state") == "value" else None


def index_runs(root, max_depth=4):
    """Every run's record (run_meta.json or run_meta_<d>.json) under ``root`` (to ``max_depth`` levels), as
    ``{run_dir, run_id, date, doc_title, finalized, recaptures}``; an
    unreadable one is listed with ``error`` rather than dropped."""
    runs = []
    root = os.path.normpath(root)
    if not os.path.isdir(root):
        return runs
    for dirpath, dirnames, filenames in os.walk(root):
        depth = os.path.relpath(dirpath, root).count(os.sep) + (0 if dirpath == root else 1)
        if depth > max_depth:
            dirnames[:] = []
            continue
        if not any(n == output_layout.RUN_META_V1
                   or (n.startswith(output_layout.RUN_META_PREFIX) and n.endswith(".json"))
                   for n in filenames):
            continue
        dirnames[:] = []          # a run directory holds no runs
        path, why = output_layout.find_run_meta(dirpath)
        if path is None:
            runs.append({"run_dir": dirpath, "error": why})
            continue
        try:
            meta = _load_json(path)
            runs.append({"run_dir": dirpath, "run_id": meta.get("run_id"),
                         "date": meta.get("date"), "doc_title": _value(meta.get("doc_title")),
                         "source_path": source_path_of(meta),
                         "finalized": meta.get("finalized") is True,
                         "recaptures": meta.get("recaptures") or []})
        except (IOError, OSError, ValueError, AttributeError) as ex:
            runs.append({"run_dir": dirpath, "error": "{0}: {1}".format(type(ex).__name__, ex)})
    runs.sort(key=lambda r: r["run_dir"])
    return runs


CLOUD_PATH_PREFIXES = ("Autodesk Docs://", "BIM 360://", "A360://")


def source_path_of(meta):
    """The LOCAL file a run was captured from, when its record says: the
    batch's ``opened_from.full_path``, else ``doc_path`` unless that is a
    cloud path (``Autodesk Docs://...``, which names no local file). None
    when the run records no local path; a title match then stands alone."""
    opened = meta.get("opened_from") or {}
    if opened.get("full_path"):
        return str(opened["full_path"])
    path = _value(meta.get("doc_path"))
    if not path or str(path).startswith(CLOUD_PATH_PREFIXES):
        return None
    return str(path)


def same_file(a, b):
    """Two local paths name one file (case and separators folded)."""
    if not a or not b:
        return False

    def norm(p):
        # Windows paths: case and separator folded on every platform.
        return os.path.normpath(str(p).replace("\\", "/")).lower()
    return norm(a) == norm(b)


def run_meta_of(run_dir):
    """The run's record, or None with the reason."""
    path, why = output_layout.find_run_meta(run_dir)
    if path is None:
        return None, why
    try:
        return _load_json(path), None
    except (IOError, OSError, ValueError) as ex:
        return None, "{0}: {1}".format(type(ex).__name__, ex)


# --- the plan ---------------------------------------------------------------------------

def build_plan(entries, output_root, recapture_runs=None, run_index=None, mode="both",
               skip_captured=True, view_types=DEFAULT_VIEW_TYPES):
    """PURE. One job per manifest file, or none.

    * mode ``recapture``: a job only for a file some run of ``recapture_runs``
      (the ``runs`` list of recapture_lists.json, each with ``run_dir`` and
      ``recapture`` ids) was captured from -- matched by the run's recorded
      ``doc_title`` (from ``run_index``) against the file's stem;
    * mode ``capture``: a job for every file, unless ``skip_captured`` and a
      finalized run in ``run_index`` was captured from it;
    * mode ``both``: a recapture job where one applies, else a capture job.

    Runs of ``recapture_runs`` that name views but match no file are listed
    under ``unmatched_runs``; files given no job under ``skipped``.
    """
    if mode not in ("recapture", "capture", "both"):
        raise BatchPlanError("mode must be recapture, capture or both, not {0!r}".format(mode))
    run_index = list(run_index or [])
    by_dir = dict((os.path.normpath(r["run_dir"]), r) for r in run_index if "run_dir" in r)
    titled = [r for r in run_index if r.get("doc_title")]
    # A title is not a durable identity: two archives in different projects
    # may share a file name (Codex, PR #236). A run that records the local
    # file it was captured from is matched on that file; a run that does not
    # is matched on its title ONLY when exactly one manifest file carries it.
    stems = {}
    for entry in entries:
        stems[entry["stem"]] = stems.get(entry["stem"], 0) + 1
    jobs, skipped, matched_dirs = [], [], set()
    for entry in entries:
        stem = entry["stem"]
        recaptures, ambiguous = [], []
        for run in recapture_runs or []:
            ids = [int(v) for v in (run.get("recapture") or [])]
            if not ids:
                continue
            indexed = by_dir.get(os.path.normpath(run.get("run_dir") or ""))
            title = indexed.get("doc_title") if indexed else None
            if title is None or not title_matches(stem, title):
                continue
            recorded = indexed.get("source_path")
            if recorded:
                if not same_file(recorded, entry["full_path"]):
                    continue           # the run names another file of this title
            elif stems[stem] > 1:
                ambiguous.append(run.get("run_id"))
                continue
            matched_dirs.add(os.path.normpath(run["run_dir"]))
            recaptures.append({"run_dir": run["run_dir"], "run_id": run.get("run_id"),
                               "doc_title": title, "view_ids": ids})
        job = {"key": entry["relative_path"], "full_path": entry["full_path"],
               "display_name": entry["display_name"], "stem": stem}
        if ambiguous and mode in ("recapture", "both"):
            skipped.append({"key": entry["relative_path"],
                            "reason": "ambiguous: {0} manifest files share the title {1!r} and "
                                      "run(s) {2} record no source file".format(
                                          stems[stem], stem, ambiguous)})
            continue
        if recaptures and mode in ("recapture", "both"):
            job.update(kind="recapture", recapture=recaptures)
            jobs.append(job)
            continue
        if mode == "recapture":
            skipped.append({"key": entry["relative_path"], "reason": "no run to recapture"})
            continue
        captured, unsure = captured_from(entry, titled, stems[stem] > 1)
        if skip_captured and captured:
            skipped.append({"key": entry["relative_path"],
                            "reason": "already captured: {0}".format(
                                ", ".join(r["run_dir"] for r in captured))})
            continue
        if skip_captured and unsure:
            skipped.append({"key": entry["relative_path"],
                            "reason": "ambiguous: {0} manifest files share the title {1!r} and "
                                      "finalized run(s) {2} of that title record no source "
                                      "file".format(stems[stem], stem,
                                                    [r.get("run_id") for r in unsure])})
            continue
        date = archive_date(stem)
        # The folder the run goes into is the one selected (Greg, 2026-10-19),
        # not one derived from the manifest: a derived <project>/<model with
        # version and size> made every archive version its own folder and
        # the run paths 60+ characters longer (output_layout's path limit).
        job.update(kind="capture",
                   output_dir=output_root,
                   date_override=date,
                   date_source="archive_name" if date else "capture_day",
                   view_types=list(view_types))
        jobs.append(job)
    unmatched = [{"run_dir": r.get("run_dir"), "run_id": r.get("run_id"),
                  "view_ids": [int(v) for v in r.get("recapture") or []],
                  "doc_title": (by_dir.get(os.path.normpath(r.get("run_dir") or "")) or {}).get(
                      "doc_title")}
                 for r in recapture_runs or []
                 if r.get("recapture") and os.path.normpath(r.get("run_dir") or "") not in matched_dirs]
    return {"schema": PLAN_SCHEMA, "mode": mode, "output_root": output_root,
            "jobs": jobs, "skipped": skipped, "unmatched_runs": unmatched}


def captured_from(entry, runs, title_shared):
    """``(captured, unsure)``: the finalized runs captured from this file --
    by the recorded source file when the run names one, by title alone only
    when no other manifest file carries it -- and the finalized runs of the
    same title that name no file while another file shares the title (the
    same rule as the recapture match; Codex, PR #236)."""
    captured, unsure = [], []
    for run in runs:
        if not run.get("finalized") or not title_matches(entry["stem"], run.get("doc_title")):
            continue
        recorded = run.get("source_path")
        if recorded:
            if same_file(recorded, entry["full_path"]):
                captured.append(run)
        elif title_shared:
            unsure.append(run)
        else:
            captured.append(run)
    return captured, unsure


def load_plan(path):
    plan = _load_json(path)
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise BatchPlanError("{0} is not a {1} plan".format(path, PLAN_SCHEMA))
    if not isinstance(plan.get("jobs"), list):
        raise BatchPlanError("{0} carries no jobs list".format(path))
    for job in plan["jobs"]:
        if job.get("kind") not in ("capture", "recapture"):
            raise BatchPlanError("job {0!r} has kind {1!r}".format(job.get("key"), job.get("kind")))
    return plan


# --- resume ------------------------------------------------------------------------------

def job_done(job):
    """Whether the job's outcome is already in the files: a capture, when a
    finalized run under the job's output_dir was captured from the file; a
    recapture, when every run's run_meta lists a ``recaptures`` entry whose
    view_ids cover the job's. Returns (done, detail)."""
    if job["kind"] == "capture":
        # Under the job's own output directory (one per project and model),
        # a run of this title that names no source file is this file's.
        captured, _unsure = captured_from(job, index_runs(job["output_dir"], max_depth=1), False)
        if captured:
            return True, "captured as {0}".format(captured[0]["run_dir"])
        return False, None
    pending = []
    for target in job["recapture"]:
        meta, err = run_meta_of(target["run_dir"])
        if meta is None:
            pending.append(target["run_dir"])
            continue
        # Covered by SUCCESSFUL outcomes only: a view whose recapture failed
        # or was refused keeps its old files and stays pending (Codex, PR
        # #236). An entry's view_ids lists what was asked, not what was done.
        succeeded = set()
        for entry in meta.get("recaptures") or []:
            refused = set(int(v) for v in entry.get("refused_view_ids") or [])
            for vid, status in (entry.get("outcomes") or {}).items():
                if status == "success" and int(vid) not in refused:
                    succeeded.add(int(vid))
        if not set(int(v) for v in target["view_ids"]) <= succeeded:
            pending.append(target["run_dir"])
    if pending:
        return False, None
    return True, "recaptured already"


def pending_jobs(plan, limit=None):
    """(pending, done): the plan's jobs split by job_done, pending cut to
    ``limit``."""
    pending, done = [], []
    for job in plan["jobs"]:
        is_done, detail = job_done(job)
        (done if is_done else pending).append(dict(job, done_detail=detail) if is_done else job)
    if limit:
        pending = pending[:int(limit)]
    return pending, done


# --- views --------------------------------------------------------------------------------

def _view_type_name(view):
    try:
        vt = view.ViewType
    except Exception:  # noqa: BLE001 -- recorded below as unreadable
        return None
    try:
        return vt.ToString()
    except Exception:  # noqa: BLE001
        return str(vt)


def select_views(views, view_types=DEFAULT_VIEW_TYPES):
    """PURE over an iterable of views. Every non-template view whose ViewType
    name is in ``view_types``, with the record run_meta carries as
    ``view_selection``: the rule, counts per type kept and excluded,
    templates, unreadable."""
    wanted = set(view_types)
    kept, counts, excluded, templates, unreadable = [], {}, {}, 0, 0
    for view in views:
        try:
            if bool(getattr(view, "IsTemplate", False)):
                templates += 1
                continue
        except Exception:  # noqa: BLE001
            unreadable += 1
            continue
        name = _view_type_name(view)
        if name is None:
            unreadable += 1
            continue
        if name in wanted:
            kept.append(view)
            counts[name] = counts.get(name, 0) + 1
        else:
            excluded[name] = excluded.get(name, 0) + 1
    record = {"rule": "every non-template view whose ViewType is in view_types",
              "view_types": sorted(wanted), "kept": counts, "excluded": excluded,
              "templates": templates, "unreadable": unreadable, "count": len(kept)}
    return kept, record


def collect_views(doc):
    """The document's View elements (Revit)."""
    from Autodesk.Revit.DB import FilteredElementCollector, View
    return list(FilteredElementCollector(doc).OfClass(View))


# --- the loop -------------------------------------------------------------------------------

def stamp_run_meta(run_dir, key, record):
    """``run_meta.json[key] = record``, written atomically; the reason when
    it cannot be."""
    meta, err = run_meta_of(run_dir)
    if meta is None:
        return err
    meta[key] = record
    write_run_meta(meta, run_dir)
    return None


def opened_record(job, how, doc_title=None, path_name=None):
    """How the batch opened the document for this job (run_meta
    ``opened_from``)."""
    return {"source": "batch_capture", "manifest_key": job["key"],
            "full_path": job["full_path"], "title": doc_title, "path_name": path_name,
            "detach": how.get("detach"), "worksets": how.get("worksets"),
            "audit": how.get("audit", False)}


def _log_path(output_root, clock_id):
    return os.path.join(output_root, "{0}{1}.json".format(BATCH_LOG_PREFIX, clock_id))


def run_batch(plan, open_doc, close_doc, capture, recapture, limit=None, log=print,
              now=None, write_log=True):
    """Run the plan's pending jobs in order. Injected:

    * ``open_doc(job) -> (doc, how)``: opens the file; ``how`` is the
      opened_record's input (detach, worksets, audit);
    * ``close_doc(doc)``: closes it (called for the PREVIOUS document once the
      next is open, as Revit refuses to close its only document; the last one
      at the end, best effort);
    * ``capture(doc, job) -> {"run_dirs": [...], ...}``;
    * ``recapture(doc, job) -> {"run_dirs": [...], "refused": [...], ...}``.

    Every job's outcome goes into the durable log after that job, and each
    run directory a job wrote gains ``opened_from``. Returns the log record.
    """
    pending, done = pending_jobs(plan, limit=limit)
    clock_id = clock_stamp(now)
    record = {"schema": BATCH_LOG_SCHEMA, "started": clock_id, "plan_mode": plan.get("mode"),
              "jobs": [], "already_done": [{"key": j["key"], "detail": j.get("done_detail")}
                                           for j in done],
              "ok": 0, "failed": 0, "finished": False}
    output_root = plan.get("output_root") or "."
    log_path = _log_path(output_root, clock_id)

    def _persist():
        if not write_log:
            return
        if not os.path.isdir(output_root):
            os.makedirs(output_root)
        tmp = log_path + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
        os.replace(tmp, log_path)

    _persist()
    prev = None
    for index, job in enumerate(pending):
        label = "[{0}/{1}] {2} ({3})".format(index + 1, len(pending), job["display_name"],
                                             job["kind"])
        log(label)
        entry = {"key": job["key"], "kind": job["kind"], "status": "failed", "run_dirs": []}
        doc = None
        try:
            doc, how = open_doc(job)
            if doc is None:
                raise RuntimeError("open_doc returned no document for {0}".format(job["full_path"]))
            entry["opened"] = opened_record(job, how, getattr(doc, "Title", None),
                                            getattr(doc, "PathName", None))
            if prev is not None:
                try:
                    close_doc(prev)
                except Exception as ex:  # noqa: BLE001 -- recorded, not fatal
                    entry["close_previous_failed"] = "{0}: {1}".format(type(ex).__name__, ex)
                prev = None
            result = recapture(doc, job) if job["kind"] == "recapture" else capture(doc, job)
            entry["result"] = dict((k, v) for k, v in (result or {}).items()
                                   if k not in ("view_summaries",))
            entry["run_dirs"] = list((result or {}).get("run_dirs") or [])
            entry["stamp_failures"] = {}
            for run_dir in entry["run_dirs"]:
                err = stamp_run_meta(run_dir, "opened_from", entry["opened"])
                if err:
                    entry["stamp_failures"][run_dir] = err
            entry["status"] = "ok"
            record["ok"] += 1
            log("  OK: {0}".format(", ".join(entry["run_dirs"]) or "(no run directory reported)"))
        except Exception as ex:  # noqa: BLE001 -- the batch goes on; the log has it
            import traceback
            entry["error"] = "{0}: {1}".format(type(ex).__name__, ex)
            entry["traceback"] = traceback.format_exc()
            record["failed"] += 1
            log("  FAILED: {0}".format(entry["error"]))
        if doc is not None:
            prev = doc
        record["jobs"].append(entry)
        _persist()
    if prev is not None:
        try:
            close_doc(prev)
            record["last_document_closed"] = True
        except Exception as ex:  # noqa: BLE001
            record["last_document_closed"] = "{0}: {1}".format(type(ex).__name__, ex)
    record["finished"] = True
    record["log_path"] = log_path if write_log else None
    _persist()
    return record
