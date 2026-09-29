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

RUN_META_SCHEMA = "vop.run_meta.v1"
RUN_META_FILENAME = "run_meta.json"
EXPORTER_VERSION = "vop_interwoven"


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


def build_run_meta(cfg, doc, run_id, date_str, view_ids, config_hash):
    """The run's record at its start. ``config_hash`` is csv_export's
    compute_config_hash(cfg) -- the value views_core.ConfigHash carries."""
    return {
        "schema": RUN_META_SCHEMA,
        "finalized": False,
        "run_id": run_id,
        "date": date_str,
        "doc_title": _read(lambda: doc.Title) if doc is not None else _unavailable("no document"),
        "doc_path": _read(lambda: doc.PathName) if doc is not None else _unavailable("no document"),
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


def write_run_meta(meta, output_dir):
    path = os.path.join(output_dir, RUN_META_FILENAME)
    with open(path, "w") as handle:
        json.dump(meta, handle, indent=2, sort_keys=True, default=str)
    return path
