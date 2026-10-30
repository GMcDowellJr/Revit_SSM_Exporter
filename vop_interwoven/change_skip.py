"""Hash-and-skip, phase 2: carry an unchanged view's capture forward.

docs/DESIGN_CHANGE_SKIP.md is the design (S-6 .. S-13). OFF by default
(``skip_unchanged``, S-12). When on, before any view is captured:

  1. the BASELINE is the latest finalized run, in the same output root, of an
     earlier as-of date in the same model series (``series_key`` of the
     document title) whose fingerprints are this basis (S-6.2);
  2. each view's verdict is ``view_fingerprint.skip_verdict`` -- the one
     function the validator scores -- and a view may skip only when its type
     is in ``skip_view_types``. The default is drafting views and legends:
     the types that held on held-out data, where model views did not
     (issue #251);
  3. a seeded AUDIT sample of the would-skip views is captured anyway (S-13:
     ``skip_audit_fraction``, at least ``skip_audit_min_per_type`` per type);
  4. every ``skip_full_refresh_every_n_runs``-th run of the series captures
     everything (S-8; 0 = never), and a series named in the output root's
     ``skip_suspended.json`` captures everything until a person clears it.

The views left are captured as usual. AFTER the run is finalized, each
skipped view's capture files are COPIED from its SOURCE capture (the run that
really captured it: one hop, S-11) into this run under the same names, each
copy verified by sha256 (S-10), and the view is DECLARED: run_meta.views[]
``capture_status: "unchanged"`` with ``carried_from``, its views_core row with
CaptureStatus ``unchanged``, and run_meta ``skip`` with the plan. A copy that
cannot be made or verified is declared ``failed`` (``carry_failed: ...``), so
find_recapture_views lists it for a recapture: readers never infer a skip
from a missing file, and never see a half-carried view as captured.

Pure parts take plain values; the Revit and file parts are marked.
"""

import hashlib
import json
import math
import os
import random
import re
import shutil
import time

SCHEMA = "vop.change_skip.v1"
UNCHANGED = "unchanged"
# The run_meta capture statuses whose files in the run ARE its measure of the
# view: captured now, or carried byte-identical from the source capture and
# declared (S-11). Readers that tie a sidecar to its run use this one tuple.
MEASURED_STATUSES = ("success", UNCHANGED)
CARRY_FAILED = "carry_failed"
SUSPENSION_FILE = "skip_suspended.json"
DEFAULTS = {
    "skip_unchanged": False,
    # Drafting views and legends only (issue #251, docs/DESIGN_CHANGE_SKIP.md
    # "Future issues"): the fingerprint missed a stair path label change on a
    # floor plan, so no model-bearing type skips yet.
    "skip_view_types": ["DraftingView", "Legend"],
    "skip_full_refresh_every_n_runs": 7,
    "skip_audit_fraction": 0.10,
    "skip_audit_min_per_type": 3,
}


# ======================================================================
# PURE
# ======================================================================

def parse_settings(raw):
    """PURE. The skip settings with every default filled; a value of the wrong
    shape raises ValueError (a typo must not quietly turn skipping on or off)."""
    raw = dict(raw or {})
    unknown = sorted(k for k in raw if k not in DEFAULTS)
    if unknown:
        raise ValueError("unknown skip settings: {0}".format(unknown))
    out = dict(DEFAULTS)
    out.update(raw)
    if not isinstance(out["skip_unchanged"], bool):
        raise ValueError("skip_unchanged must be true or false")
    types = out["skip_view_types"]
    if not isinstance(types, (list, tuple)) or not all(isinstance(t, str) for t in types):
        raise ValueError("skip_view_types must be a list of ViewType names")
    out["skip_view_types"] = sorted(set(types))
    n = out["skip_full_refresh_every_n_runs"]
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError("skip_full_refresh_every_n_runs must be an integer >= 0")
    f = out["skip_audit_fraction"]
    if isinstance(f, bool) or not isinstance(f, (int, float)) or not 0 <= f <= 1:
        raise ValueError("skip_audit_fraction must be between 0 and 1")
    m = out["skip_audit_min_per_type"]
    if isinstance(m, bool) or not isinstance(m, int) or m < 0:
        raise ValueError("skip_audit_min_per_type must be an integer >= 0")
    return out


_DETACHED = "_detached"
_DATE_SUFFIX = re.compile(r"_\d{4}-\d{2}-\d{2}$")
_VERSION_TAIL = re.compile(r"_V\d+(_.*)?$")


def series_key(title):
    """PURE. The model a document title belongs to, for finding its baseline:
    the title without Revit's detached suffix, the trailing ``_YYYY-MM-DD``
    and the ``_V<n>...`` archive tail
    (``KSRF_Hosp_Interior_AR_V57_3.8 GB_2025-12-20_detached`` ->
    ``KSRF_Hosp_Interior_AR``). None when the title has no archive date: such
    a document has no series, so it gets no baseline and nothing skips."""
    if isinstance(title, dict):
        title = title.get("value") if title.get("state") == "value" else None
    if not title:
        return None
    text = str(title)
    if text.endswith(_DETACHED):
        text = text[:-len(_DETACHED)]
    if not _DATE_SUFFIX.search(text):
        return None
    text = _VERSION_TAIL.sub("", _DATE_SUFFIX.sub("", text))
    return text or None


def series_runs(index, key, before_date):
    """PURE. The finalized runs of series ``key`` with an as-of date before
    ``before_date``, oldest first (``index``: batch_capture.index_runs rows)."""
    rows = [r for r in index if not r.get("error") and r.get("finalized")
            and r.get("date") and str(r["date"]) < str(before_date)
            and series_key(r.get("doc_title")) == key]
    return sorted(rows, key=lambda r: (str(r["date"]), str(r.get("run_id") or "")))


def full_refresh_due(prior_skips, every_n):
    """PURE. ``(due, runs_since_full)``. ``prior_skips``: the series' earlier
    runs' run_meta ``skip`` records, oldest first (None for a run that did
    not skip). A run that did not skip, or was a full refresh, is a full
    capture; the runs that skipped after the last one are counted, and the
    run after ``every_n - 1`` of them is the next full refresh. 0 = never."""
    since = 0
    for rec in reversed(list(prior_skips)):
        if not rec or not rec.get("enabled") or rec.get("full_refresh") \
                or not rec.get("carried_view_ids"):
            break
        since += 1
    return (every_n > 0 and since >= every_n - 1), since


def _seed(text):
    return int(hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:12], 16)


def audit_sample(by_type, fraction, min_per_type, seed):
    """PURE. The audit: from each type's would-skip ids, ``max(ceil(fraction *
    n), min_per_type)`` of them (all when fewer), drawn with ``seed``
    (reproducible: the same ids for the same seed and population)."""
    rng = random.Random(_seed(seed))
    chosen = set()
    for vtype in sorted(by_type):
        ids = sorted(int(v) for v in by_type[vtype])
        k = min(len(ids), max(int(math.ceil(fraction * len(ids))), min_per_type))
        chosen.update(rng.sample(ids, k))
    return chosen


def plan_skips(cur_facts, cur_views, base, settings, seed, suspended=None, full_refresh=False):
    """PURE. Which views skip.

    ``cur_facts`` / ``cur_views``: this run's ``run_facts`` and fingerprint
    ``views``; ``base``: ``{"facts", "views", "statuses"}`` of the baseline,
    or None. Returns ``{"skip", "audit", "verdicts", "by_type", "reason"}``:
    ``skip`` the ids to carry, ``audit`` the would-skip ids captured anyway,
    ``verdicts`` {id: first failing rule or "would_skip" or "type_not_enabled"},
    ``reason`` why nothing skips (None when the plan ran)."""
    from .view_fingerprint import skip_verdict
    out = {"skip": [], "audit": [], "verdicts": {}, "by_type": {}, "reason": None}
    if suspended:
        out["reason"] = "suspended"
        return out
    if full_refresh:
        out["reason"] = "full_refresh"
        return out
    if base is None:
        out["reason"] = "no_baseline"
        return out
    allowed = set(settings["skip_view_types"])
    would = {}
    for vid, cur in sorted((cur_views or {}).items()):
        vtype = cur.get("view_type")
        verdict = skip_verdict(base["facts"], cur_facts, base["views"].get(vid), cur,
                               base["statuses"].get(vid))
        rule = verdict["rule"] or "would_skip"
        if verdict["would_skip"] and vtype not in allowed:
            rule = "type_not_enabled"
        out["verdicts"][vid] = rule
        row = out["by_type"].setdefault(vtype or "(unknown)", {})
        row[rule] = row.get(rule, 0) + 1
        if rule == "would_skip":
            would.setdefault(vtype, []).append(int(vid))
    audit = audit_sample(would, settings["skip_audit_fraction"],
                         settings["skip_audit_min_per_type"], seed)
    out["audit"] = sorted(audit)
    out["skip"] = sorted(v for ids in would.values() for v in ids if v not in audit)
    return out


def source_of(base_run, base_entry):
    """PURE. The SOURCE capture of a view in the baseline (S-11: one hop): the
    baseline run itself, or, when the baseline only carried the view, the
    run it was carried from."""
    if (base_entry or {}).get("capture_status") == UNCHANGED:
        return dict(base_entry["carried_from"])
    return {"run_dir": base_run["run_dir"], "run_id": base_run.get("run_id"),
            "as_of_date": base_run.get("date")}


def carried_entry(base_entry, source, fingerprint, baseline_run_id, files):
    """PURE. The run_meta.views[] entry a carried view gets: the baseline's
    entry (name, capture_passes, ...) with the outcome declared."""
    entry = dict((k, v) for k, v in (base_entry or {}).items()
                 if k not in ("recapture", "carried_from", "skip_check", "fingerprint",
                              "carried_files"))
    entry.update(capture_status=UNCHANGED, capture_failure_reason="",
                 carried_from=dict(source), fingerprint=fingerprint,
                 skip_check={"baseline_run_id": baseline_run_id, "rule": None},
                 carried_files=list(files))
    return entry


def failed_carry_entry(base_entry, view_id, why):
    entry = {"view_id": int(view_id),
             "view_name": (base_entry or {}).get("view_name"),
             "capture_status": "failed",
             "capture_failure_reason": "{0}: {1}".format(CARRY_FAILED, why)}
    return entry


# ======================================================================
# FILES
# ======================================================================

def _capture_dir(run_dir):
    from .output_layout import CAPTURE_DIR, V1, V2
    for layout in (V2, V1):
        path = os.path.join(run_dir, CAPTURE_DIR[layout])
        if os.path.isdir(path):
            return path
    return None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def view_capture_files(capture_dir, view_id):
    """The view's capture files in ``capture_dir``: ``<stem>.json/.tiff`` and
    ``<stem>_anno.json/.tiff`` where present, with ``<stem>`` ending
    ``_<view_id>``. Two stems for one id raises."""
    vid = str(int(view_id))
    names = os.listdir(capture_dir)
    stems = set()
    for n in names:
        for suffix in (".json", "_anno.json"):
            if n.endswith("_" + vid + suffix):
                stems.add(n[:-len(suffix)])
    if len(stems) > 1:
        raise ValueError("view {0}: more than one capture stem in {1}: {2}".format(
            vid, capture_dir, sorted(stems)))
    if not stems:
        return []
    stem = stems.pop()
    return [n for n in (stem + ".json", stem + ".tiff", stem + "_anno.json",
                        stem + "_anno.tiff") if n in names]


def carry_view(source_run_dir, dest_run_dir, view_id):
    """Copy the view's capture files from its source run into this run,
    verified by sha256. Returns the names; raises ValueError naming why it
    could not (nothing to copy, a name already taken, a mismatch, a path past
    the 260-character Windows limit) after removing anything it copied."""
    from .output_layout import CAPTURE_DIR, layout_of_run, NEW_RUN_LAYOUT
    src_dir = _capture_dir(source_run_dir)
    if src_dir is None:
        raise ValueError("no capture folder in {0}".format(source_run_dir))
    names = view_capture_files(src_dir, view_id)
    if not any(n.endswith(".json") for n in names):
        raise ValueError("no capture of view {0} in {1}".format(view_id, src_dir))
    dest_dir = _capture_dir(dest_run_dir) or os.path.join(
        dest_run_dir, CAPTURE_DIR[layout_of_run(dest_run_dir) or NEW_RUN_LAYOUT])
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    done = []
    try:
        for n in names:
            src, dst = os.path.join(src_dir, n), os.path.join(dest_dir, n)
            if len(os.path.abspath(dst)) > 259:
                raise ValueError("{0} is past the 260-character path limit".format(dst))
            if os.path.exists(dst):
                raise ValueError("{0} already exists in this run".format(n))
            shutil.copy2(src, dst)
            done.append(dst)
            if _sha256(src) != _sha256(dst):
                raise ValueError("{0}: the copy does not match its source".format(n))
    except (IOError, OSError, ValueError) as ex:
        for path in done:
            try:
                os.remove(path)
            except OSError as rm:
                ex = ValueError("{0}; and {1} could not be removed: {2}".format(ex, path, rm))
        raise ValueError(str(ex))
    return names


def read_suspension(output_root, key):
    """The suspension record for series ``key`` in the output root's
    ``skip_suspended.json``, or None. An unreadable file suspends every
    series: unknown never skips."""
    path = os.path.join(output_root, SUSPENSION_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as handle:
            record = json.load(handle)
    except (IOError, OSError, ValueError) as ex:
        return {"reason": "{0} unreadable: {1}: {2}".format(SUSPENSION_FILE,
                                                              type(ex).__name__, ex)}
    if not isinstance(record, dict):
        return {"reason": "{0} is not an object".format(SUSPENSION_FILE)}
    return record.get(key) or record.get("*")


def load_baseline(index, key, as_of_date):
    """``(baseline, prior_skip_records, reason)``: the series' latest earlier
    finalized run with a current-basis fingerprints file, as
    ``{"run", "meta", "fingerprints"}``; the series' run_meta ``skip``
    records oldest first (for the full refresh); or None with the reason."""
    from . import output_layout
    from .batch_capture import run_meta_of
    from .view_fingerprint import BASIS_VERSION
    rows = series_runs(index, key, as_of_date)
    metas = []
    for row in rows:
        meta, _err = run_meta_of(row["run_dir"])
        metas.append(meta)
    prior = [(m or {}).get("skip") for m in metas]
    if not rows:
        return None, prior, "no earlier finalized run of series {0!r}".format(key)
    row, meta = rows[-1], metas[-1]
    if meta is None:
        return None, prior, "baseline run_meta unreadable: {0}".format(row["run_dir"])
    stamp = meta.get("view_fingerprints") or {}
    name = stamp.get("file") or output_layout.fingerprints_name(
        output_layout.census_file_date(meta, row["run_dir"]))
    path = os.path.join(row["run_dir"], name)
    if not os.path.isfile(path):
        return None, prior, "baseline {0} has no {1}".format(row["run_dir"], name)
    with open(path) as handle:
        fp = json.load(handle)
    if fp.get("basis_version") != BASIS_VERSION:
        return None, prior, "baseline fingerprints are basis {0}, not {1}".format(
            fp.get("basis_version"), BASIS_VERSION)
    return {"run": row, "meta": meta, "fingerprints": fp}, prior, None


# ======================================================================
# THE RUN (Revit document, run directory)
# ======================================================================

def plan_for_run(doc, cfg, output_root, identity, fingerprints, settings):
    """The skip plan for a run about to be captured: a dict that is always
    complete, recorded as run_meta ``skip``. Never raises for a missing or
    unreadable baseline: that skips nothing, with the reason."""
    from .batch_capture import index_runs
    from .run_meta import provenance_fields, revit_version
    from .streaming import run_config_hash
    from .view_fingerprint import run_facts
    t0 = time.time()
    record = {"schema": SCHEMA, "enabled": True, "settings": settings,
              "series_key": None, "baseline": None, "full_refresh": False,
              "runs_since_full": None, "suspended": None, "reason": None,
              "audit_view_ids": [], "skip_view_ids": [], "carried_view_ids": [],
              "carry_failed": [], "summary": None}
    title = getattr(doc, "Title", None)
    key = series_key(title)
    record["series_key"] = key
    if key is None:
        record["reason"] = "no_series: the document title {0!r} has no archive date".format(title)
        return record, None
    as_of = identity["as_of_date"]
    base, prior, why = load_baseline(index_runs(output_root, max_depth=1), key, as_of)
    due, since = full_refresh_due(prior, settings["skip_full_refresh_every_n_runs"])
    record.update(full_refresh=due, runs_since_full=since,
                  suspended=read_suspension(output_root, key))
    # The hash the run WILL record: the run forces some values before hashing
    # (streaming.force_streaming_settings). Hashing ``cfg`` as given never
    # matched a run (2026-01-24: every view config_hash_differs).
    cur_meta = {"config_hash": run_config_hash(cfg),
                "exporter_version": provenance_fields()["exporter_version"],
                "revit_version": revit_version(doc)}
    cur_facts = run_facts(cur_meta, fingerprints)
    base_in = None
    if base is not None:
        record["baseline"] = {"run_dir": base["run"]["run_dir"],
                              "run_id": base["run"].get("run_id"),
                              "as_of_date": base["run"].get("date"),
                              "doc_title": base["run"].get("doc_title")}
        base_in = {"facts": run_facts(base["meta"], base["fingerprints"]),
                   "views": base["fingerprints"].get("views") or {},
                   "statuses": dict((str(v.get("view_id")), v.get("capture_status"))
                                    for v in base["meta"].get("views") or [])}
    plan = plan_skips(cur_facts, fingerprints.get("views"), base_in, settings,
                      seed=identity["run_id"], suspended=record["suspended"],
                      full_refresh=due)
    record.update(reason=plan["reason"] or (None if base is not None else why),
                  audit_view_ids=plan["audit"], skip_view_ids=plan["skip"],
                  summary={"by_type": plan["by_type"]},
                  plan_ms=round((time.time() - t0) * 1000.0, 1))
    if base is None and plan["reason"] == "no_baseline":
        record["reason"] = "no_baseline: {0}".format(why)
    return record, base


def carry_into_run(doc, cfg, run_dir, record, base, fingerprints):
    """After the run is finalized: copy each skipped view's files from its
    source capture, then declare it in run_meta and views_core. Returns the
    run_meta ``skip`` record as written."""
    from . import output_layout
    from .batch_capture import run_meta_of
    from .csv_export import get_stage_a_core_csv_header, stage_a_view_result_to_core_row
    from .recapture import merge_views_core_rows
    from .run_meta import single_pass_views, write_run_meta
    meta, err = run_meta_of(run_dir)
    if meta is None:
        raise ValueError("no run_meta to declare carried views in: {0}".format(err))
    base_entries = dict((str(v.get("view_id")), v) for v in base["meta"].get("views") or [])
    new_entries, rows = [], []
    for vid in record["skip_view_ids"]:
        key = str(vid)
        base_entry = base_entries.get(key)
        source = source_of(base["run"], base_entry)
        try:
            files = carry_view(source["run_dir"], run_dir, vid)
        except ValueError as ex:
            record["carry_failed"].append({"view_id": vid, "reason": str(ex)})
            entry = failed_carry_entry(base_entry, vid, ex)
            status, reason = entry["capture_status"], entry["capture_failure_reason"]
        else:
            fp = ((fingerprints.get("views") or {}).get(key) or {}).get("fingerprint")
            entry = carried_entry(base_entry, source, fp, base["run"].get("run_id"), files)
            record["carried_view_ids"].append(vid)
            status, reason = UNCHANGED, ""
        new_entries.append(entry)
        row = stage_a_view_result_to_core_row({"view_id": vid}, cfg, doc, meta.get("run_id"),
                                              config_hash=meta.get("config_hash"))
        row.update(CaptureStatus=status, CaptureFailureReason=reason, ElapsedSec="0.000")
        rows.append(row)
    requested = list(meta.get("views_requested") or [])
    requested.extend(v for v in record["skip_view_ids"] if v not in requested)
    meta["views_requested"] = requested
    meta["views"] = list(meta.get("views") or []) + new_entries
    if "single_pass_views" in meta or any("capture_passes" in v for v in meta["views"]):
        meta["single_pass_views"] = single_pass_views(meta["views"])
    record["summary"] = dict(record.get("summary") or {}, carried=len(record["carried_view_ids"]),
                             carry_failed=len(record["carry_failed"]),
                             audited=len(record["audit_view_ids"]))
    meta["skip"] = record
    if rows:
        csv_name = "views_core_{0}.csv".format(output_layout.census_file_date(meta, run_dir))
        merge_views_core_rows(os.path.join(run_dir, csv_name), rows,
                              get_stage_a_core_csv_header())
    write_run_meta(meta, run_dir)       # LAST: the record names only what is on disk
    return record
