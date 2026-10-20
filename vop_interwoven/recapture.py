"""Recapture in place: capture listed views of an EXISTING run again, into
that run's own directory, replacing their files and rows, and say so.

Why (2026-10-12). Run 2025-06-21__20261008T093539 holds 52 views whose
registration ticks never rendered (a crop sketched rotated against the
view's axes; tools/notes/RUN_2025-06-21_20261008T093539_ROTATION.md). The
capture now places its ticks inside the crop loop, and only a new capture
can give those views a usable annotation layer; the other 102 views of the
run need nothing. ``tools/find_recapture_views.py`` lists which views of
which runs, as ``recapture_lists.json``; this module takes that list.

What a recapture is, and is not:

* it runs against the SAME document the run was captured from: the open
  document's title must equal the run's ``run_meta.doc_title``, or the run
  is refused. The as-of date stays the archive's;
* it continues the run's identity: the same ``run_id`` and directory
  (``run_meta.check_run_directory`` with ``continuing=True``), so every
  sidecar and views_core row keys into the one run_meta;
* only views the run REQUESTED may be recaptured; an id it did not is
  refused;
* each recaptured view's capture files (``<stem>.json``, ``.tiff``,
  ``_anno.json``, ``_anno.tiff``) are rewritten; its views_core row is
  replaced by ``(RunId, ViewId)``; its ``views[]`` entry in run_meta.json
  is replaced and gains a ``recapture`` record; the run gains
  ``recaptures: [...]``. Nothing of any other view is touched. The analysis
  files a recaptured view had (``_anno.registered.json``, ``.grid.*``,
  ``.kinds.*``) are left for the analysis tools to overwrite: their hashes
  no longer match, so the roll-up reports them stale until
  ``register_stage_a_annotation.py``, ``stage_a_grid.py`` and
  ``stage_a_kinds.py`` are run again;
* a recapture that would reproduce the failure -- any tick still outside
  the crop loop before the export -- is refused for that view by the
  registered capture (``failure_reason: recapture_refused``), and the
  view's files, its views_core row and its run_meta entry are left as they
  were; the refusal is recorded under ``recaptures[].refused_view_ids``;
* the per-view ``views_diagnostics_<date>.json`` and
  ``vop_view_element_map_<date>.json`` entries are merged by the pipeline
  as for any view of the run (both files are keyed by the run's id, and a
  recaptured view's entry replaces its own);
* run_meta.json is written back ONCE, merged, when the recapture's chunk
  finalizes (it is a finished run throughout; no ``finalized: false`` is
  written). A recapture interrupted before that leaves the run's record as
  it was, new files for the views already captured (each stamped
  ``recapture``), and the chunk's ``views_core_<date>.csv.recapture_rows.tmp``
  beside the run's CSV -- the sign to look for;
* it is a different exporter build from the run's: every recaptured sidecar
  carries ``registration_marks.recapture`` with the build that made it, and
  run_meta's ``recaptures`` and per-view ``recapture`` keys say which views
  come from which build. ``run_meta.exporter_version`` stays the original
  run's.
"""

import csv
import json
import os

from . import output_layout
from .run_meta import (
    _replace, clock_stamp, exporter_version, git_commit, view_outcomes,
)

RECAPTURE_REASON = "ticks_outside_crop_loop"
RECAPTURE_SCHEMA = "vop.recapture.v1"
# The failure_reason the registered capture gives a view it refused to
# recapture (stage_a_registered_capture.export_registered_stage_a_view).
RECAPTURE_REFUSED = "recapture_refused"


class RecaptureRefused(ValueError):
    """The recapture cannot be shown to be this run's; nothing is written."""


def _value(record):
    return record.get("value") if isinstance(record, dict) and record.get(
        "state") == "value" else None


def _vid(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def load_run_meta(run_dir):
    """The run's record (run_meta.json or run_meta_<d>.json), or
    RecaptureRefused."""
    path, why = output_layout.find_run_meta(run_dir)
    if path is None:
        raise RecaptureRefused("{0}; it cannot be shown to be a run's".format(why))
    try:
        with open(path) as handle:
            meta = json.load(handle)
    except (IOError, OSError, ValueError) as ex:
        raise RecaptureRefused("{0} is unreadable: {1}: {2}".format(path, type(ex).__name__, ex))
    if not isinstance(meta, dict):
        raise RecaptureRefused("{0} is not a record".format(path))
    return meta


def check_run_dir_exists(entry):
    """RecaptureRefused when a list entry's ``run_dir`` is not a directory.

    ``run_dir`` is the path the run RECORDED (tools/find_recapture_views.py),
    which a folder rename leaves stale (2026-10-19: KSRF/Hops_Interior_AR
    renamed Hosp_Interior_AR, every run_meta still naming the old path). Never
    a silent fallback: the refusal names both paths, and the list is what to
    correct -- the runs stay as captured."""
    run_dir = entry.get("run_dir")
    if run_dir and os.path.isdir(run_dir):
        return
    scanned = entry.get("scanned_dir")
    raise RecaptureRefused(
        "the run directory {0!r} does not exist{1}; correct run_dir in the recapture "
        "list (a renamed folder leaves the run's recorded path stale)".format(
            run_dir, "" if not scanned else
            " (the finder scanned it at {0!r}{1})".format(
                scanned, ", which exists" if os.path.isdir(scanned) else ", which does not "
                                                                         "exist either")))


def recapture_plan(meta, view_ids, doc_title):
    """PURE. ``{"run_id", "as_of_date", "view_ids"}`` for a recapture of
    ``view_ids`` into the run ``meta`` describes, or RecaptureRefused:

    * the run is not finalized, or has no run_id or date;
    * the open document's title is not the run's ``doc_title`` (a recapture
      from another archive would be another run's capture under this run's
      id) -- ``batch_capture.title_matches``: the title, or the title with
      Revit's ``_detached`` suffix, since the batch runner opens detached;
    * an id is not in ``views_requested``, or the list is empty.
    """
    if meta.get("finalized") is not True:
        raise RecaptureRefused("the run is not finalized (finalized={0!r}); a recapture "
                               "continues a finished run only".format(meta.get("finalized")))
    if not meta.get("run_id") or not meta.get("date"):
        raise RecaptureRefused("the run records no run_id or date")
    recorded = _value(meta.get("doc_title"))
    if recorded is None:
        raise RecaptureRefused("the run records no document title to check the open "
                               "document against")
    from .batch_capture import title_matches
    if not title_matches(recorded, doc_title):
        raise RecaptureRefused("the open document is {0!r}; the run was captured from "
                               "{1!r}".format(doc_title, recorded))
    ids = [_vid(v) for v in (view_ids or [])]
    if not ids:
        raise RecaptureRefused("no view ids to recapture")
    requested = set(_vid(v) for v in meta.get("views_requested") or [])
    unknown = sorted(v for v in ids if v not in requested)
    if unknown:
        raise RecaptureRefused("{0} id(s) the run never requested: {1}".format(
            len(unknown), unknown[:10]))
    return {"run_id": meta["run_id"], "as_of_date": meta["date"], "view_ids": ids}


def recapture_record(meta, view_ids, reason=RECAPTURE_REASON, now=None):
    """The record every recaptured sidecar and run_meta carry: WHEN (the
    execution clock, to the second), WHICH build, WHY, which views,
    and what build the run itself was."""
    version, source = exporter_version()
    return {
        "schema": RECAPTURE_SCHEMA,
        "captured_at": clock_stamp(now),
        "exporter_version": version,
        "exporter_source": source,
        "git_commit": git_commit(),
        "reason": reason,
        "view_ids": [_vid(v) for v in view_ids],
        "replaces_exporter_version": meta.get("exporter_version"),
    }


def merge_recapture_into_run_meta(meta, record, view_summaries):
    """``meta`` with the recaptured views' outcomes replaced and marked.

    Each outcome from ``view_summaries`` (the same shape
    ``finalize_run_meta`` writes) replaces the ``views[]`` entry of its
    view_id and gains ``recapture: {captured_at, exporter_version}``; every
    other entry is kept as it was. The run gains one ``recaptures`` entry
    (the record plus the outcomes' statuses). ``finalized`` stays true: the
    run was finished before and is finished after.
    """
    outcomes = view_outcomes(view_summaries)
    stamp = {"captured_at": record["captured_at"],
             "exporter_version": record["exporter_version"],
             "reason": record.get("reason")}
    refused = [o for o in outcomes if outcome_refused(o)]
    by_id = dict((_vid(o.get("view_id")), dict(o, recapture=stamp))
                 for o in outcomes if not outcome_refused(o))
    views = []
    seen = set()
    for entry in meta.get("views") or []:
        vid = _vid(entry.get("view_id"))
        if vid in by_id and vid not in seen:
            views.append(by_id[vid])
            seen.add(vid)
        elif vid in seen:
            continue        # a duplicate outcome for a recaptured id is dropped
        else:
            views.append(entry)
    for vid, outcome in by_id.items():
        if vid not in seen:
            views.append(outcome)     # requested, outcome never recorded before
    meta["views"] = views
    # One recapture is ONE entry, however many chunks (batches) wrote it:
    # a chunk finalizing under the same record extends the entry.
    entries = meta.setdefault("recaptures", [])
    entry = None
    for candidate in entries:
        if (candidate.get("captured_at") == record["captured_at"]
                and candidate.get("exporter_version") == record["exporter_version"]):
            entry = candidate
    if entry is None:
        entry = dict(record, outcomes={}, refused_view_ids=[])
        entries.append(entry)
    entry["outcomes"].update((str(o.get("view_id")), o.get("capture_status")) for o in outcomes)
    entry["refused_view_ids"].extend(
        v for v in (_vid(o.get("view_id")) for o in refused)
        if v not in entry["refused_view_ids"])
    meta["finalized"] = True
    return meta


def outcome_refused(outcome):
    """True for a view whose recapture the capture REFUSED before exporting
    (``recapture_refused``: its ticks would still lie outside the crop
    loop). Its files, its views_core row and its run_meta entry stay as
    they were; the refusal is recorded under ``recaptures`` only."""
    return str(outcome.get("capture_failure_reason") or "").startswith(RECAPTURE_REFUSED)


def row_refused(row):
    """The views_core-row form of outcome_refused."""
    return str(row.get("CaptureFailureReason") or "").startswith(RECAPTURE_REFUSED)


def merge_views_core_rows(csv_path, new_rows, header):
    """Replace, in the run's views_core CSV, the rows whose ``(RunId,
    ViewId)`` a new row carries; keep every other row and the row order;
    append new rows whose key was absent. Written atomically. Returns
    ``(replaced, appended)``. A missing file is created from the header."""
    new_by_key = {}
    for row in new_rows:
        new_by_key[(str(row.get("RunId")), str(row.get("ViewId")))] = row
    existing = []
    if os.path.isfile(csv_path):
        with open(csv_path, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            existing = list(reader)
            if reader.fieldnames and list(reader.fieldnames) != list(header):
                header = list(reader.fieldnames)
    out, replaced, done = [], 0, set()
    for row in existing:
        key = (str(row.get("RunId")), str(row.get("ViewId")))
        if key in new_by_key and key not in done:
            out.append(new_by_key[key])
            done.add(key)
            replaced += 1
        elif key in done:
            continue
        else:
            out.append(row)
    appended = 0
    for key, row in new_by_key.items():
        if key not in done:
            out.append(row)
            appended += 1
    tmp = csv_path + ".recapture.tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header), extrasaction="ignore")
        writer.writeheader()
        for row in out:
            writer.writerow(row)
    _replace(tmp, csv_path)
    return replaced, appended


def _chunks(items, size):
    if not size or size <= 0:
        return [list(items)]
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def load_recapture_lists(source):
    """``recapture_lists.json`` (tools/find_recapture_views.py) as its
    ``runs`` list: a path, a JSON text, or the parsed dict."""
    if isinstance(source, dict):
        payload = source
    else:
        text = str(source)
        if os.path.isfile(text):
            with open(text) as handle:
                payload = json.load(handle)
        else:
            payload = json.loads(text)
    runs = payload.get("runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        raise RecaptureRefused("the recapture list carries no 'runs' list")
    return runs


def run_recapture(doc, lists, cfg, batch_size=None, reason=RECAPTURE_REASON, runner=None):
    """Recapture every run entry of ``lists`` that names views, one run at a
    time, in chunks of ``batch_size`` views, each chunk one call of
    ``run_vop_pipeline_streaming`` with ``recapture=`` the run's record.

    Each entry's optional ``reason`` is stamped instead of ``reason``.

    Returns ``{"runs": [...], "views_processed", "views_failed",
    "annotation_passes_failed", "csv_rows_written", "view_summaries",
    "refused": [...]}``; a run that is refused (wrong document, unknown id,
    unfinalized) is listed under ``refused`` with the reason and skipped,
    never partly done. ``runner`` is injectable for tests.
    """
    if runner is None:
        from .streaming import run_vop_pipeline_streaming as runner
    doc_title = getattr(doc, "Title", None)
    summary = {"runs": [], "refused": [], "views_processed": 0, "views_failed": 0,
               "annotation_passes_failed": 0, "csv_rows_written": 0,
               "view_summaries": [], "png_files": [], "view_raster_files": []}
    for entry in load_recapture_lists(lists):
        ids = entry.get("recapture") or []
        run_dir = entry.get("run_dir")
        if not ids:
            summary["runs"].append({"run_dir": run_dir, "run_id": entry.get("run_id"),
                                    "views": 0, "skipped": "nothing to recapture"})
            continue
        try:
            check_run_dir_exists(entry)
            meta = load_run_meta(run_dir)
            plan = recapture_plan(meta, ids, doc_title)
        except RecaptureRefused as ex:
            summary["refused"].append({"run_dir": run_dir, "run_id": entry.get("run_id"),
                                       "reason": str(ex)})
            continue
        # A run entry may name its own reason (a list made for another
        # defect than the rotated crop, e.g. annotation_lattice_mismatch).
        record = recapture_record(meta, plan["view_ids"], reason=entry.get("reason") or reason)
        root = os.path.dirname(os.path.normpath(run_dir))
        run_summary = {"run_dir": run_dir, "run_id": plan["run_id"],
                       "captured_at": record["captured_at"], "views": len(plan["view_ids"]),
                       "chunks": 0, "views_processed": 0, "views_failed": 0}
        for chunk in _chunks(plan["view_ids"], batch_size):
            result = runner(doc=doc, view_ids=chunk, cfg=cfg, output_dir=root,
                            export_png=True, export_csv=True, export_json=False,
                            export_view_raster=False, pixels_per_cell=10,
                            date_override=plan["as_of_date"], run_id=plan["run_id"],
                            recapture=record)
            run_summary["chunks"] += 1
            for key in ("views_processed", "views_failed"):
                run_summary[key] += result.get(key, 0)
            for key in ("views_processed", "views_failed", "annotation_passes_failed",
                        "csv_rows_written"):
                summary[key] += result.get(key, 0)
            summary["view_summaries"].extend(result.get("view_summaries", []))
        summary["runs"].append(run_summary)
        summary["run_dir"] = run_dir
    return summary
