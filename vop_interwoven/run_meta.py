"""run_meta.json: ONE record per run of everything that is not per-view.

C9 (Greg, 2026-09-29): the run id, date, document, exporter version and git
commit, Revit version, the whole Config and its hash, and the view list live
here once. Per-view output (sidecars, views_core rows) keeps only per-view
facts; views_core carries RunId + ConfigHash as the keys into this file.

Written at the START of a run with ``finalized: false`` -- so a run that dies
still leaves its metadata -- and rewritten by finalize_run_meta() with the
per-view outcomes and ``finalized: true``. A reader that finds
``finalized: false`` is reading an interrupted run, not a clean one
(CLAUDE.md defect class 4: a record serialised before it is finished must
say so).

Every field that is READ from the environment is three-valued: a value, or
{"state": "unavailable", "reason": ...}. Never a guessed default.
"""

import json
import os

from . import __version__ as _PACKAGE_VERSION

RUN_META_SCHEMA = "vop.run_meta.v1"
RUN_META_FILENAME = "run_meta.json"
# The exporter's NAME and its VERSION are separate keys. exporter_version used
# to hold the package name, which identified nothing. It is now the package's
# own __version__, the one version string the package declares (not a copy).
# The commit that produced a run is git_commit; this is the release version.
EXPORTER_NAME = "vop_interwoven"
EXPORTER_VERSION = _PACKAGE_VERSION


def _parse_override_date(date_override):
    """The datetime a date override names, or None when it names none (a
    tag, or nothing). Accepts a datetime, or a string "2026-10-01",
    "20261001" or ISO "2026-10-01T12:34:56". The one parser behind both
    output_date_str() and run_identity(), so the two cannot disagree on
    which overrides are dates."""
    from datetime import datetime
    if isinstance(date_override, datetime):
        return date_override
    if isinstance(date_override, str) and date_override.strip():
        s = date_override.strip()
        parsers = [lambda v: datetime.strptime(v, "%Y-%m-%d")]
        if len(s) == 8 and s.isdigit():
            parsers.append(lambda v: datetime.strptime(v, "%Y%m%d"))
        if hasattr(datetime, "fromisoformat"):   # absent on IronPython 2
            parsers.append(datetime.fromisoformat)
        for parse in parsers:
            try:
                return parse(s)
            except ValueError:
                continue   # not this shape; the next parser, else a tag
    return None


def output_date_str(date_override, now=None):
    """The date part of every dated per-run filename (views_core_<d>.csv,
    views_diagnostics_<d>.json, vop_view_element_map_<d>.json).

    A datetime, or a string that parses as one ("2026-10-01", "20261001",
    ISO "2026-10-01T12:34:56"), names the files by its date, YYYY-MM-DD --
    never the raw string, whose colons Windows refuses in a filename. A
    string that does not parse is a tag ("PR_221") and names the files as
    given. Nothing means today. ONE function, so the streaming exporter and
    the pipeline cannot name one run's files differently again -- the
    pipeline used to fall back to today on a tag.
    """
    from datetime import datetime
    parsed = _parse_override_date(date_override)
    if parsed is not None:
        return parsed.strftime("%Y-%m-%d")
    if isinstance(date_override, str) and date_override.strip():
        return date_override.strip()
    return (now or _clock()).strftime("%Y-%m-%d")


RUN_ID_CLOCK_FORMAT = "%Y%m%dT%H%M%S"


def _clock():
    """The execution clock run ids are minted from (a seam for tests)."""
    from datetime import datetime
    return datetime.now()


def run_identity(date_override, now=None, run_id=None):
    """``{run_id, as_of_date, run_tag}`` for one run: WHO the run is, kept
    apart from WHAT DATE its data is as of.

    * ``run_id`` is minted from the execution clock, ``now`` (default
      datetime.now()), as YYYYMMDDTHHMMSS, plus ``_<tag>`` when the override
      is a tag. A date override never enters it: with the override in it,
      two re-runs of one archive snapshot (or two archive models with the
      same snapshot date) shared ``<date>T000000`` and one run's files
      replaced the other's. A ``run_id`` passed in (a later thinrunner
      batch) is returned as given, never re-minted.
    * ``as_of_date`` (YYYY-MM-DD) is the override's date when it parses as
      one, else the execution date -- for a passed-in ``run_id`` of the
      minted shape, the date it was minted on, so a batched run that crosses
      midnight keeps one as-of date and one run directory.
    * ``run_tag`` is the override when it is a tag ("PR_221"), else None.
    """
    from datetime import datetime
    parsed = _parse_override_date(date_override)
    tag = None
    if parsed is None and date_override is not None:
        text = str(date_override).strip()
        tag = text or None
    if run_id:
        clock = None
        try:
            clock = datetime.strptime(str(run_id)[:15], RUN_ID_CLOCK_FORMAT)
        except ValueError:
            clock = None   # not a minted id; the as-of date falls back to now
        clock = clock or now or _clock()
    else:
        clock = now or _clock()
        run_id = clock.strftime(RUN_ID_CLOCK_FORMAT)
        if tag:
            run_id = "{0}_{1}".format(run_id, tag)
    return {"run_id": run_id,
            "as_of_date": (parsed or clock).strftime("%Y-%m-%d"),
            "run_tag": tag}


def identity_file_date_str(date_override, identity):
    """output_date_str() for a run whose identity is already minted: with no
    override, the files are named by the identity's as-of date rather than by
    a second read of the clock, which a run straddling midnight would answer
    with the next day (Codex, PR #230)."""
    from datetime import datetime
    return output_date_str(
        date_override, now=datetime.strptime(identity["as_of_date"], "%Y-%m-%d"))


def run_directory(root, identity):
    """``<root>/<as_of_date>__<run_id>``: the ONE directory a run writes into.
    Derived from the identity alone, so every batch of one run (same run_id,
    same override) resolves to the same directory."""
    return os.path.join(root, "{0}__{1}".format(identity["as_of_date"],
                                                identity["run_id"]))


class RunDirectoryConflict(Exception):
    """The run directory already holds another run, or something that cannot
    be shown to be this run."""


def check_run_directory(run_dir, run_id, continuing=False):
    """Refuse, BEFORE any capture work, to write into a directory that is not
    this run's:

    * absent -> proceed;
    * holds a run_meta.json naming this ``run_id`` -> proceed, but only when
      ``continuing`` (the caller passed an existing run's id in: a batch
      continuing its own run). A run that has just MINTED its id and finds
      its directory already there is a second run started in the same
      second with the same override -- it is refused, not merged;
    * holds a run_meta.json naming another run_id, or an unreadable one ->
      RunDirectoryConflict naming both;
    * exists with no run_meta.json -> RunDirectoryConflict (nothing shows
      whose it is).
    """
    if not os.path.exists(run_dir):
        return
    path = os.path.join(run_dir, RUN_META_FILENAME)
    if not os.path.isfile(path):
        raise RunDirectoryConflict(
            "run directory {0} already exists and holds no {1}, so it cannot be "
            "shown to be run {2}'s; refusing to write into it".format(
                run_dir, RUN_META_FILENAME, run_id))
    try:
        with open(path) as handle:
            existing = json.load(handle).get("run_id")
    except (IOError, OSError, ValueError, AttributeError) as ex:
        raise RunDirectoryConflict(
            "run directory {0} holds an unreadable {1} ({2}: {3}); refusing to "
            "write run {4} into it".format(run_dir, RUN_META_FILENAME,
                                           type(ex).__name__, ex, run_id))
    if existing != run_id:
        raise RunDirectoryConflict(
            "run directory {0} holds run {1!r}; this run is {2!r}; refusing to "
            "write into it".format(run_dir, existing, run_id))
    if not continuing:
        raise RunDirectoryConflict(
            "run directory {0} already holds run {1!r}, and this run minted the "
            "same id rather than continuing it (two runs started in the same "
            "second); refusing to write into it".format(run_dir, run_id))


def _value(v):
    return {"state": "value", "value": v}


def _unavailable(reason):
    return {"state": "unavailable", "reason": reason}


def _read(fn):
    try:
        return _value(fn())
    except Exception as ex:
        return _unavailable("{0}: {1}".format(type(ex).__name__, ex))


def git_commit(start_dir=None):
    """The checkout's HEAD commit, read from ``.git`` without a subprocess
    (none is available inside Revit). A deployed copy with no ``.git`` is
    "unavailable" with that as the reason, never an empty string."""
    here = os.path.abspath(start_dir or os.path.dirname(__file__))
    for _ in range(4):
        git_dir = os.path.join(here, ".git")
        if os.path.isdir(git_dir):
            try:
                with open(os.path.join(git_dir, "HEAD")) as handle:
                    head = handle.read().strip()
                if not head.startswith("ref: "):
                    return _value(head)
                ref = head[5:]
                ref_path = os.path.join(git_dir, *ref.split("/"))
                if os.path.exists(ref_path):
                    with open(ref_path) as handle:
                        return _value(handle.read().strip())
                packed = os.path.join(git_dir, "packed-refs")
                if os.path.exists(packed):
                    with open(packed) as handle:
                        for line in handle:
                            parts = line.split()
                            if len(parts) == 2 and parts[1] == ref:
                                return _value(parts[0])
                return _unavailable("HEAD names {0}, which resolves to no commit".format(ref))
            except (IOError, OSError) as ex:
                return _unavailable("{0}: {1}".format(type(ex).__name__, ex))
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return _unavailable("no .git directory above {0} (a deployed copy)".format(
        os.path.dirname(__file__)))


def revit_version(doc):
    if doc is None:
        return _unavailable("no document")

    def _v():
        app = doc.Application
        return {"version_number": str(app.VersionNumber),
                "version_build": str(app.VersionBuild),
                "version_name": str(app.VersionName)}
    return _read(_v)


def build_run_meta(cfg, doc, run_id, date_str, view_ids, config_hash, run_tag=None,
                   run_dir=None):
    """The run's record at its start. ``config_hash`` is csv_export's
    compute_config_hash(cfg) -- the value views_core.ConfigHash carries.
    ``date_str`` is the run's AS-OF date (run_identity()), not its
    execution date; ``run_dir`` the run directory (run_directory())."""
    return {
        "schema": RUN_META_SCHEMA,
        "finalized": False,
        "run_id": run_id,
        "date": date_str,
        # The caller's non-date override (thinrunner's tag, e.g. "PR_221"),
        # or None. Never written into "date".
        "run_tag": run_tag,
        "run_dir": run_dir,
        "doc_title": _read(lambda: doc.Title) if doc is not None else _unavailable("no document"),
        "doc_path": _read(lambda: doc.PathName) if doc is not None else _unavailable("no document"),
        "exporter": EXPORTER_NAME,
        "exporter_version": EXPORTER_VERSION,
        "git_commit": git_commit(),
        "revit_version": revit_version(doc),
        "config_hash": config_hash,
        "config_hash_basis": ("root_cache.compute_config_hash: sha256 of "
                              "Config.to_dict() minus the view_cache_* location "
                              "keys, first 8 hex characters"),
        "config": cfg.to_dict(),
        "views_requested": [_view_id_int(v) for v in (view_ids or [])],
        "views": [],
    }


def _view_id_int(v):
    value = getattr(getattr(v, "Id", None), "IntegerValue", None)
    if value is None:
        value = getattr(v, "IntegerValue", v)
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def finalize_run_meta(meta, view_summaries):
    """The run's per-view outcomes, then ``finalized: true``."""
    from .csv_export import stage_a_capture_status
    views = []
    for s in view_summaries or []:
        status, reason = stage_a_capture_status(s)
        if status == "registration_failed" and not reason:
            # A view SUMMARY carries the fault names, not the record.
            reason = ";".join(str(f) for f in s.get("registration_faults") or [])
        views.append({"view_id": s.get("view_id"), "view_name": s.get("view_name"),
                      "capture_status": status, "capture_failure_reason": reason})
    meta["views"] = views
    meta["finalized"] = True
    return meta


def merge_run_metas(metas):
    """One run's record from its batches' records (thinrunner batching).

    Every batch must carry the same run id and config hash -- a batch that
    does not is a different run, and merging it would lie -- so a mismatch
    raises. The run is finalized only if every batch was.
    """
    metas = [m for m in metas if m]
    if not metas:
        raise ValueError("no batch run_meta to merge")
    first = dict(metas[0])
    for m in metas[1:]:
        for key in ("run_id", "config_hash"):
            if m.get(key) != first.get(key):
                raise ValueError("batch run_meta disagrees on {0}: {1!r} vs {2!r}".format(
                    key, first.get(key), m.get(key)))
    first["views_requested"] = [v for m in metas for v in m.get("views_requested") or []]
    first["views"] = [v for m in metas for v in m.get("views") or []]
    first["finalized"] = all(m.get("finalized") for m in metas)
    first["batches"] = len(metas)
    return first


def write_merged_run_meta(batch_meta_paths, output_dir, run_complete):
    """The run's root run_meta.json from the batch records written so far.

    Called after EVERY batch, before its captures are moved into
    ``output_dir``: written only at the end, a later batch that raised left
    the already-relocated captures with no run_meta.json beside or above
    them, so find_run_meta() could not restore their requested config and
    the interrupted run had no record at all (Codex, PR #221). Until
    ``run_complete`` the record says ``finalized: false`` whatever the
    batches say -- the run is not finished while batches remain.
    Returns the path, or None when no batch has written a record."""
    metas = []
    for path in batch_meta_paths:
        with open(path) as handle:
            metas.append(json.load(handle))
    if not metas:
        return None
    merged = merge_run_metas(metas)
    if not run_complete:
        merged["finalized"] = False
    return write_run_meta(merged, output_dir)


def write_run_meta(meta, output_dir):
    """Write run_meta.json ATOMICALLY: to a sibling temporary file, then
    replace. Opening the final path with "w" truncated the readable
    finalized:false record before the finalized one was serialised, so a
    process that stopped mid-write left empty or partial JSON (review,
    PR #221) -- the opposite of the interrupted-run record promised above.
    The temporary file is removed if the write fails."""
    import tempfile
    path = os.path.join(output_dir, RUN_META_FILENAME)
    fd, tmp_path = tempfile.mkstemp(prefix=".run_meta_", suffix=".json.tmp",
                                    dir=output_dir)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(meta, handle, indent=2, sort_keys=True, default=str)
        _replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return path


def _replace(src, dst):
    """os.replace, which IronPython 2 lacks. Its fallback removes the old file
    first, so it is not atomic there -- stated, not hidden: the window is one
    rename, not a whole serialisation."""
    replace = getattr(os, "replace", None)
    if replace is not None:
        replace(src, dst)
        return
    if os.path.exists(dst):
        os.remove(dst)
    os.rename(src, dst)
