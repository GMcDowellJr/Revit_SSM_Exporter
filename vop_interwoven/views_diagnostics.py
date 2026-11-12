"""The run's ``views_diagnostics_<d>.json``, written ONCE (2026-11-09).

The pipeline runs once per view under streaming, and every call used to read
the whole file, merge it (``merge_prior_view_diagnostics``) and rewrite it
with ``indent=2``: view k cost O(k), the run O(n^2), and the rewrite was not
atomic. The run's owner (the StreamingExporter) now hands the pipeline a
journal instead, as E2 did for the view-element map (core/element_cache.py):

* per view, ONE JSON line is APPENDED to
  ``views_diagnostics_<d>.journal.jsonl`` (``element_cache.journal_path``,
  the same naming rule as the map's journal) -- constant cost, nothing
  already in the file is read. Each line carries ``schema``, ``run_id`` (the
  exporter's), ``view_id`` (the entry's key), ``entry`` (exactly as the
  pipeline built it) and ``metadata`` (the file metadata that call would
  have written). A call with NO entry still appends one line, with
  ``view_id: null`` and no ``entry``: the per-call path wrote the file even
  then, so the compacted file must exist and carry that call's metadata;
* at finalize the journal is compacted (``compact_views_diagnostics_file``):
  the existing file, merged by ``merge_prior_view_diagnostics`` -- the one
  merge rule, so a different run's file is replaced and the recapture case
  (the same run id) merges -- then every line in order, a view seen twice
  taking its LAST entry ("this call wins"; the element map unions, this
  file must not). The metadata is the last line's, as the last call's was.
  Written to ``<file>.tmp`` and ``os.replace``d; the journal is removed only
  after the replace. A compaction that fails keeps the journal and says so;
  a journal that cannot be removed after a good write is kept and recorded
  (``journal_not_removed``). The exporter writes every report into run_meta
  as ``views_diagnostics_compaction``.

A run that stops short leaves the journal: every view whose append returned
is in it; a torn LAST line is dropped and counted, an unreadable earlier
line refuses (``element_cache.read_journal_lines``, the shared torn-tail
rule). ``compact_views_diagnostics(run_dir, run_id)`` needs no live
exporter. Nothing calls it on a stopped run yet: it is groundwork for a
resume path, which does not exist.

A caller with no StreamingExporter has no journal and keeps the per-call
merge in pipeline.process_document_views, except that an existing file it
cannot read is no longer silently replaced: it is left in place, the
call's entries go to ``views_diagnostics_<d>.unmerged.json``
(``unmerged_path``, merged by the same rule if it exists) and the refusal
is recorded in Diagnostics, naming both files.
"""

import json
import os

from .core.element_cache import append_journal_lines, journal_path, read_journal_lines

JOURNAL_SCHEMA = "vop.views_diagnostics_journal.v1"
FILE_PREFIX = "views_diagnostics_"
UNMERGED_SUFFIX = ".unmerged.json"


def merge_prior_view_diagnostics(existing_payload, views, exporter_run_id):
    """``views`` with the prior entries of an existing views_diagnostics file
    merged in, when that file belongs to THIS run.

    Streaming calls process_document_views once per view, so each call must
    add to the file rather than replace it. But the file is named by date
    only, and merging ANY file already there pulled an earlier run's views
    into this one (the same leak the view-element map had; PR #221). Given
    an exporter run id, only a file stamped with the same id is merged; any
    other is replaced. With none (a caller without a StreamingExporter), the
    file is merged as before, since replacing it would keep only the last
    per-view call.
    """
    if not isinstance(existing_payload, dict):
        return views
    if exporter_run_id is not None and (
            (existing_payload.get("metadata") or {}).get("exporter_run_id")
            != exporter_run_id):
        return views
    existing_views = existing_payload.get("views", {})
    if not isinstance(existing_views, dict):
        return views
    merged = dict(existing_views)
    merged.update(views)
    return merged


def unmerged_path(diag_path):
    """Where a per-call write goes when ``diag_path`` exists but cannot be
    read: ``views_diagnostics_<d>.unmerged.json`` beside it."""
    base = diag_path[:-len(".json")] if diag_path.endswith(".json") else diag_path
    return base + UNMERGED_SUFFIX


def read_payload(path):
    """The parsed file at ``path``; raises OSError / ValueError when it cannot
    be read or parsed (the caller decides: refuse, never guess)."""
    with open(path, "r") as handle:
        return json.load(handle)


def write_payload_atomic(path, payload):
    """``json.dump(payload, indent=2)`` to ``<path>.tmp``, then os.replace.
    A failed write removes the temp file and re-raises; ``path`` is left as
    it was."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _parse_line(rec):
    meta = rec["metadata"]
    if not isinstance(meta, dict):
        raise TypeError("metadata is {0}, not a dict".format(type(meta).__name__))
    vid = rec["view_id"]
    if vid is None:
        return rec.get("run_id"), None, None, meta
    entry = rec["entry"]
    return rec.get("run_id"), str(vid), entry, meta


def read_views_diagnostics_journal(path, run_id):
    """``(lines, report)``: ``[(view_id or None, entry, metadata)]`` in append
    order. Lines of another run id are skipped and counted (``other_run``);
    torn lines follow ``read_journal_lines``."""
    records, report = read_journal_lines(path, _parse_line)
    out = []
    report["other_run"] = 0
    for line_run_id, vid, entry, meta in records:
        if line_run_id != run_id:
            report["other_run"] += 1
            continue
        out.append((vid, entry, meta))
    return out, report


def compact_views_diagnostics_file(diag_path, run_id):
    """Compact ``diag_path``'s journal into it; remove the journal only once
    the file is replaced. ``(ok, report)``; ``ok`` None when there is no
    journal. Raises nothing for an unreadable journal or existing file: both
    are a refusal, ``ok`` False, the journal kept."""
    jpath = journal_path(diag_path)
    if not os.path.exists(jpath):
        return None, {"journal": None}
    try:
        lines, report = read_views_diagnostics_journal(jpath, run_id)
    except (OSError, ValueError) as ex:
        return False, {"journal": jpath, "error": "{0}: {1}".format(type(ex).__name__, ex)}
    report["journal"] = jpath
    if not lines:
        report["error"] = "no readable line of run {0!r}; journal kept".format(run_id)
        return False, report
    existing = None
    if os.path.exists(diag_path):
        try:
            existing = read_payload(diag_path)
        except (OSError, ValueError) as ex:
            report["error"] = ("existing file unreadable ({0}: {1}); not replaced, "
                               "journal kept".format(type(ex).__name__, ex))
            return False, report
    views = {}
    for vid, entry, _meta in lines:
        if vid is not None:
            views[vid] = entry           # last line wins, first position kept
    payload = {"metadata": lines[-1][2],
               "views": merge_prior_view_diagnostics(existing, views, run_id)}
    try:
        write_payload_atomic(diag_path, payload)
    except (OSError, ValueError, TypeError) as ex:
        report["error"] = "{0}: {1}; journal kept".format(type(ex).__name__, ex)
        return False, report
    report["views"] = len(payload["views"])
    try:
        os.remove(jpath)
    except OSError as ex:
        # The file is written and complete; only the cleanup failed (Windows:
        # another process holds the journal). Kept and recorded, never raised
        # out of the exporter's finalize: compacting it again gives the same
        # file (every line is already in it, and the last entry still wins).
        report["journal_not_removed"] = "{0}: {1}".format(type(ex).__name__, ex)
    return True, report


def compact_views_diagnostics(run_dir, run_id):
    """Compact every views_diagnostics journal in ``run_dir`` (a finished or a
    stopped run's directory). ``{diag_path: report}``, each with ``ok``."""
    suffix = journal_path("x.json")[1:]
    out = {}
    for name in sorted(os.listdir(run_dir)):
        if name.startswith(FILE_PREFIX) and name.endswith(suffix):
            diag_path = os.path.join(run_dir, name[:-len(suffix)] + ".json")
            ok, report = compact_views_diagnostics_file(diag_path, run_id)
            report["ok"] = ok
            out[diag_path] = report
    return out


class ViewsDiagnosticsJournal(object):
    """One run's views_diagnostics, appended per view and written once.

    Owned by the StreamingExporter (``cfg._views_diagnostics_journal``,
    beside ``cfg._view_element_map_journal``); the pipeline appends to it in
    place of the per-call read-merge-rewrite, and the exporter's finalize
    calls ``finalize()``."""

    def __init__(self, run_id):
        self.run_id = run_id
        self.paths = []          # each file this run appended to, in order
        self.views_appended = 0

    def append(self, diag_path, metadata, views):
        """One line per entry of ``views`` (``{view_id: entry}``, in order), or
        one ``view_id: null`` line when there is none. Constant cost: the
        journal is opened for append and nothing in it is read."""
        if diag_path not in self.paths:
            self.paths.append(diag_path)
        head = {"schema": JOURNAL_SCHEMA, "run_id": self.run_id}
        lines = []
        for vid, entry in (views or {}).items():
            rec = dict(head, view_id=str(vid), entry=entry, metadata=metadata)
            lines.append(json.dumps(rec, separators=(",", ":")))
        if not lines:
            lines.append(json.dumps(dict(head, view_id=None, metadata=metadata),
                                    separators=(",", ":")))
        parent = os.path.dirname(diag_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        append_journal_lines(journal_path(diag_path), lines)
        self.views_appended += len(views or {})
        return True

    def finalize(self):
        """Compact every journal this run wrote. ``{diag_path: report}``; a
        file that could not be written keeps its journal."""
        out = {}
        for diag_path in self.paths:
            ok, report = compact_views_diagnostics_file(diag_path, self.run_id)
            report["ok"] = ok
            out[diag_path] = report
        return out
