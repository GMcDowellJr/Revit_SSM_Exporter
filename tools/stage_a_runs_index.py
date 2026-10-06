#!/usr/bin/env python3
"""The Stage A runs index: one row of facts per run folder under an export root.

Read-only. It never writes, renames or touches anything inside a run folder,
which is stricter than the roll-up's "never write into the capture folder".
It reads each run's own files and records what it found; it does NOT decide
which runs are good, latest or included -- selection belongs to the consumer
(Power Query / Power BI), which reads this index plus the files it names and
never has to list the export tree.

LAYOUT. ``<root>/<project>/<model>/<as_of>__<run_id>/`` holding
``run_meta.json``, the dated per-run files (``views_core_<d>.csv``,
``views_diagnostics_<d>.json``, ``vop_view_element_map_<d>.json``),
``color_id_buffer/`` and, today, ``analysis_grid/``. The roll-up
(``grid_rollup.csv`` / ``grid_rollup.summary.json``) and kinds inventory
(``kinds_inventory.csv`` / ``kinds_inventory.json``) are looked for at the
run's top level first, then in ``analysis_grid/``, and the location found is
recorded (``top`` | ``analysis_grid``).

DISCOVERY. A run folder is a folder that directly holds ``run_meta.json``.
The walk starts at ``--root``, stops descending at a run folder, never enters
``color_id_buffer/`` or ``analysis_grid/``, does not follow directory
symlinks, and stops at ``--max-depth`` (default 4; the root is depth 0). It
lists one directory at a time -- never a recursive glob (a run holds ~3,000
files). Every folder it does not index or does not descend into is listed
under ``scan.skipped`` with its reason, never dropped silently: a
``run_meta.json`` that cannot be parsed or is not an object, a run folder
whose name is not ``<YYYY-MM-DD>__<run_id>``, a reserved folder name, a
symlink, the depth cap, or a directory that cannot be listed.

IDENTITY. ``run_id`` is opaque and comes from run_meta.json; it is never
parsed. ``as_of_date`` is run_meta ``date``. The folder name is split only
for the ``chk_folder_*`` consistency facts. ``model_key`` is the path from
the root to the run folder's parent, ``/``-separated -- never ``doc_title``.

CELLS. Every value is a string. An empty cell means absent / not applicable,
never zero. Each file-derived group has a ``*_state`` (``value``, ``absent``,
``unreadable``; ``ambiguous`` for a dated file matched more than once) and a
``*_reason`` that is filled whenever the state is not ``value``. Every
``*_path_rel`` and ``run_dir_rel`` is relative to ``--root`` and uses ``/``.

OUTPUT, in ``--out`` (default ``<root>``): ``runs_index.csv`` and then
``runs_index.json`` (written last, naming the CSV's sha256; CLAUDE.md defect
class 4), each to a temporary name and atomically replaced, rebuilt from
scratch on every invocation. Rows are sorted by (model_key, as_of_date,
run_id). ``--out`` inside a run folder is refused.

    python tools/stage_a_runs_index.py --root <dir> [--out <dir>] [--max-depth 4]

Exit 0 when at least one run is indexed and every run is ``finalized`` with
rollup_state ``value``; 1 for anything else indexed; 2 when no run is
indexed, on bad arguments, or when the output cannot be written.

Standard library only. The file names of the roll-up and kinds tools are read
from those tools' own sources (``tool_constant``) rather than imported, since
importing them pulls numpy; ``tests/test_stage_a_runs_index.py`` asserts the
two agree with the imported modules.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vop_interwoven.run_meta import RUN_META_FILENAME  # noqa: E402

SCHEMA = "vop.stage_a.runs_index.v1"
TOOL_VERSION = "1.0.0"

CSV_NAME = "runs_index.csv"
JSON_NAME = "runs_index.json"

_TOOLS = Path(__file__).resolve().parent


def tool_constant(tool_file, name):
    """A module-level string constant read from a tool's source, without
    importing it (the roll-up and kinds tools import numpy; this tool is
    standard library only). Raises LookupError when it is not a plain string
    assignment, rather than guessing a name."""
    tree = ast.parse((_TOOLS / tool_file).read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            return node.value.value
    raise LookupError("{0}: no string constant {1}".format(tool_file, name))


ROLLUP_CSV = tool_constant("stage_a_grid_rollup.py", "CSV_NAME")
ROLLUP_SUMMARY = tool_constant("stage_a_grid_rollup.py", "SUMMARY_NAME")
KINDS_CSV = tool_constant("stage_a_kinds.py", "INVENTORY_CSV")
KINDS_JSON = tool_constant("stage_a_kinds.py", "INVENTORY_JSON")

CAPTURE_DIR = "color_id_buffer"
GRID_DIR = "analysis_grid"
RESERVED_DIRS = (CAPTURE_DIR, GRID_DIR)
# (top, analysis_grid): where the roll-up and kinds outputs are looked for.
LOCATIONS = (("top", ""), ("analysis_grid", GRID_DIR))

# The dated per-run files, named by run_meta.output_date_str().
DATED_FILES = (("views_core", "views_core_", ".csv"),
               ("element_map", "vop_view_element_map_", ".json"),
               ("diagnostics", "views_diagnostics_", ".json"))

FOLDER_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})__(.+)$")

COLUMNS = [
    # run identity
    "model_key", "run_dir_rel", "run_dir_name",
    "run_id", "run_tag", "as_of_date",
    "doc_title", "doc_path",
    "config_hash", "git_commit", "exporter_version", "revit_version_number",
    "run_meta_schema", "finalized",
    # run_meta views
    "views_requested_count", "views_count", "capture_status_counts",
    # roll-up
    "rollup_state", "rollup_reason", "rollup_location",
    "rollup_csv_path_rel", "rollup_summary_path_rel",
    "rollup_schema", "rollup_tool_version",
    "rollup_csv_sha256", "rollup_csv_sha256_match",
    "rollup_class_map_sha256", "rollup_class_map_version",
    "rollup_refused_runs_count", "rollup_count_invariant_difference",
    "rollup_row_status_counts", "rollup_registration_state_counts", "rollup_flag_counts",
    "rollup_run_ids",
    # kinds inventory
    "kinds_state", "kinds_reason", "kinds_location", "kinds_csv_path_rel",
    "kinds_schema", "kinds_tool_version",
    "kinds_class_map_sha256", "kinds_csv_sha256", "kinds_csv_sha256_match",
    "kinds_grid_used", "kinds_grid_denominator",
    # other per-run files
    "views_core_state", "views_core_reason", "views_core_path_rel", "views_core_rows",
    "element_map_state", "element_map_reason", "element_map_path_rel",
    "diagnostics_state", "diagnostics_reason", "diagnostics_path_rel",
    # consistency facts
    "chk_folder_date_eq_as_of", "chk_folder_run_id_eq_run_meta",
    "chk_rollup_run_id_eq_run_meta", "chk_kinds_class_map_eq_rollup",
    # group facts
    "n_runs_same_model_as_of", "n_config_hash_in_model",
]

# Columns holding JSON in the CSV; real objects in runs_index.json.
JSON_COLUMNS = ("capture_status_counts", "rollup_row_status_counts",
                "rollup_registration_state_counts", "rollup_flag_counts", "rollup_run_ids")


def _err(ex):
    return "{0}: {1}".format(type(ex).__name__, ex)


def _json_cell(obj):
    """A JSON value as one deterministic CSV cell."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _cell(value):
    """A CSV cell: empty for absent / not applicable, never a stand-in zero.
    A three-valued run_meta field (``{"state": "unavailable", ...}``) is
    absent. Booleans are ``true`` / ``false``."""
    if value is None or isinstance(value, (dict, list)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _bool_cell(value):
    return "" if value is None else ("true" if value else "false")


def _rel(path, root):
    rel = Path(path).relative_to(root).as_posix()
    return "" if rel == "." else rel


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json_object(path):
    """``(obj, None)`` or ``(None, reason)``: the file parsed as a JSON object."""
    try:
        with open(path, encoding="utf-8") as handle:
            obj = json.load(handle)
    except (OSError, ValueError) as ex:
        return None, _err(ex)
    if not isinstance(obj, dict):
        return None, "not a JSON object ({0})".format(type(obj).__name__)
    return obj, None


# --- discovery ----------------------------------------------------------------------

def discover(root, max_depth):
    """``(runs, scan)``: ``runs`` is ``[(run_dir, run_meta)]`` for every run
    folder that can be indexed; ``scan`` records what was visited and every
    folder skipped, with its reason."""
    root = Path(root)
    runs, skipped = [], []
    visited = run_folders = 0
    deepest = 0
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        visited += 1
        deepest = max(deepest, depth)
        rel = _rel(folder, root) or "."
        if (folder / RUN_META_FILENAME).is_file():
            run_folders += 1
            meta, why = _load_json_object(folder / RUN_META_FILENAME)
            if meta is None:
                skipped.append({"path_rel": rel, "reason": "run_meta.json unreadable: " + why})
            elif not FOLDER_NAME.match(folder.name):
                skipped.append({"path_rel": rel,
                                "reason": "run folder name {0!r} is not "
                                          "<YYYY-MM-DD>__<run_id>".format(folder.name)})
            else:
                runs.append((folder, meta))
            continue   # a run folder is never descended into
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError as ex:
            skipped.append({"path_rel": rel, "reason": "cannot be listed: " + _err(ex)})
            continue
        children = []
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=True):
                    continue
                is_link = entry.is_symlink()
            except OSError as ex:
                skipped.append({"path_rel": _rel(entry.path, root),
                                "reason": "cannot be inspected: " + _err(ex)})
                continue
            child_rel = _rel(entry.path, root)
            if entry.name in RESERVED_DIRS:
                skipped.append({"path_rel": child_rel,
                                "reason": "reserved folder name {0!r} outside a run "
                                          "folder; not searched".format(entry.name)})
            elif is_link:
                skipped.append({"path_rel": child_rel, "reason": "symlink; not followed"})
            elif depth + 1 > max_depth:
                skipped.append({"path_rel": child_rel,
                                "reason": "beyond --max-depth {0}; not searched".format(
                                    max_depth)})
            else:
                children.append(Path(entry.path))
        stack.extend((c, depth + 1) for c in reversed(children))
    skipped.sort(key=lambda s: s["path_rel"])
    scan = {"max_depth": max_depth, "folders_visited": visited,
            "max_depth_reached": deepest, "run_folders_found": run_folders,
            "run_folders_indexed": len(runs),
            "skipped": skipped}
    return runs, scan


# --- per-run facts ------------------------------------------------------------------

def _locate(run_dir, name):
    """``(location, path)`` of the first of ``<run>/name``,
    ``<run>/analysis_grid/name`` that is a file, else ``(None, None)``."""
    for location, sub in LOCATIONS:
        path = run_dir / sub / name if sub else run_dir / name
        if path.is_file():
            return location, path
    return None, None


def _counts_obj(value):
    """A summary's ``{"counts": {...}}`` record's counts, or None."""
    if isinstance(value, dict) and isinstance(value.get("counts"), dict):
        return value["counts"]
    return None


def _named_csv(record, record_path, default):
    """``(path or None, reason or None)``: the CSV a summary / inventory record
    names. It must be a bare file name and a file beside the record; a name
    with a separator, ``..`` or an absolute path is reported, never followed
    (a hand-edited record must not point the index outside the run)."""
    name = record.get("csv", default)
    if not isinstance(name, str) or not name or name in (".", "..") \
            or "/" in name or "\\" in name or Path(name).name != name:
        return None, "the record names csv {0!r}, which is not a file name beside " \
                     "it".format(name)
    path = record_path.parent / name
    if not path.is_file():
        return None, "{0} named by the record is not beside it".format(name)
    return path, None


def _sha_match(csv_path, recorded):
    """``(sha256 of the CSV, "true"/"false")``; empty where not computable."""
    if csv_path is None:
        return "", "", None
    try:
        actual = sha256_file(csv_path)
    except OSError as ex:
        return "", "", "{0} cannot be hashed: {1}".format(csv_path.name, _err(ex))
    if not isinstance(recorded, str) or not recorded:
        return actual, "", None
    return actual, _bool_cell(actual == recorded), None


def rollup_facts(run_dir, root):
    out = dict((c, "") for c in COLUMNS if c.startswith("rollup_"))
    objs = dict((c, None) for c in JSON_COLUMNS if c.startswith("rollup_"))
    location, summary_path = _locate(run_dir, ROLLUP_SUMMARY)
    if summary_path is None:
        loose_loc, loose = _locate(run_dir, ROLLUP_CSV)
        out["rollup_state"] = "absent"
        out["rollup_reason"] = "no {0} at the run top level or in {1}/".format(
            ROLLUP_SUMMARY, GRID_DIR)
        if loose is not None:
            out["rollup_reason"] += "; {0} present without it".format(ROLLUP_CSV)
            out["rollup_location"] = loose_loc
            out["rollup_csv_path_rel"] = _rel(loose, root)
        return out, objs
    out["rollup_location"] = location
    out["rollup_summary_path_rel"] = _rel(summary_path, root)
    summary, why = _load_json_object(summary_path)
    if summary is None:
        out["rollup_state"], out["rollup_reason"] = "unreadable", why
        return out, objs
    csv_path, csv_err = _named_csv(summary, summary_path, ROLLUP_CSV)
    if csv_path is not None:
        out["rollup_csv_path_rel"] = _rel(csv_path, root)
    actual, match, hash_err = _sha_match(csv_path, summary.get("csv_sha256"))
    out["rollup_csv_sha256"], out["rollup_csv_sha256_match"] = actual, match
    cmap = summary.get("class_map") if isinstance(summary.get("class_map"), dict) else {}
    refused = summary.get("refused_runs")
    invariant = summary.get("count_invariant")
    out.update({
        "rollup_schema": _cell(summary.get("schema")),
        "rollup_tool_version": _cell(summary.get("tool_version")),
        "rollup_class_map_sha256": _cell(cmap.get("sha256")),
        "rollup_class_map_version": _cell(cmap.get("version")),
        "rollup_refused_runs_count": _cell(refused.get("count"))
        if isinstance(refused, dict) else "",
        "rollup_count_invariant_difference": _cell(invariant.get("difference"))
        if isinstance(invariant, dict) else "",
    })
    objs["rollup_row_status_counts"] = _counts_obj(summary.get("row_status"))
    objs["rollup_registration_state_counts"] = _counts_obj(summary.get("registration_state"))
    objs["rollup_flag_counts"] = _counts_obj(summary.get("flag_counts"))
    runs = summary.get("runs")
    reasons = []
    if isinstance(runs, list):
        ids = [r.get("run_id") if isinstance(r, dict) else None for r in runs]
        if all(isinstance(i, str) for i in ids):
            objs["rollup_run_ids"] = ids
        else:
            # Recorded as found in the JSON; not used for the consistency fact.
            reasons.append("the summary's runs carry a run_id that is not a string")
    if csv_err:
        reasons.append(csv_err)
    if hash_err:
        reasons.append(hash_err)
    out["rollup_state"] = "value"
    out["rollup_reason"] = "; ".join(reasons)
    return out, objs


def kinds_facts(run_dir, root):
    out = dict((c, "") for c in COLUMNS if c.startswith("kinds_"))
    location, json_path = _locate(run_dir, KINDS_JSON)
    if json_path is None:
        out["kinds_state"] = "absent"
        out["kinds_reason"] = "no {0} at the run top level or in {1}/".format(
            KINDS_JSON, GRID_DIR)
        loose_loc, loose = _locate(run_dir, KINDS_CSV)
        if loose is not None:
            out["kinds_reason"] += "; {0} present without it".format(KINDS_CSV)
            out["kinds_location"] = loose_loc
            out["kinds_csv_path_rel"] = _rel(loose, root)
        return out
    out["kinds_location"] = location
    record, why = _load_json_object(json_path)
    if record is None:
        out["kinds_state"], out["kinds_reason"] = "unreadable", why
        return out
    csv_path, csv_err = _named_csv(record, json_path, KINDS_CSV)
    if csv_path is not None:
        out["kinds_csv_path_rel"] = _rel(csv_path, root)
    actual, match, hash_err = _sha_match(csv_path, record.get("csv_sha256"))
    cmap = record.get("class_map") if isinstance(record.get("class_map"), dict) else {}
    grids = record.get("grid_records") if isinstance(record.get("grid_records"), dict) else {}
    out.update({
        "kinds_schema": _cell(record.get("schema")),
        "kinds_tool_version": _cell(record.get("tool_version")),
        "kinds_class_map_sha256": _cell(cmap.get("sha256")),
        "kinds_csv_sha256": actual, "kinds_csv_sha256_match": match,
        "kinds_grid_used": _cell(grids.get("used")),
        "kinds_grid_denominator": _cell(grids.get("denominator")),
    })
    reasons = []
    if csv_err:
        reasons.append(csv_err)
    if hash_err:
        reasons.append(hash_err)
    out["kinds_state"] = "value"
    out["kinds_reason"] = "; ".join(reasons)
    return out


def dated_file_facts(run_dir, root, names):
    """Presence and path of each dated per-run file at the run's top level;
    ``views_core_rows`` counts the data rows of the one views_core file.
    ``names`` is the run folder's listing, or the reason it cannot be listed."""
    out = {"views_core_rows": ""}
    if isinstance(names, str):
        for key, _prefix, _ext in DATED_FILES:
            out.update({key + "_state": "unreadable", key + "_path_rel": "",
                        key + "_reason": "the run folder cannot be listed: " + names})
        return out
    for key, prefix, ext in DATED_FILES:
        found = sorted(n for n in names if n.startswith(prefix) and n.endswith(ext)
                       and len(n) > len(prefix) + len(ext) and (run_dir / n).is_file())
        out[key + "_path_rel"] = ""
        if not found:
            out[key + "_state"] = "absent"
            out[key + "_reason"] = "no {0}<d>{1} at the run top level".format(prefix, ext)
        elif len(found) > 1:
            out[key + "_state"] = "ambiguous"
            out[key + "_reason"] = "{0} files: {1}".format(len(found), ", ".join(found))
        else:
            out[key + "_state"], out[key + "_reason"] = "value", ""
            out[key + "_path_rel"] = _rel(run_dir / found[0], root)
    if out["views_core_state"] == "value":
        path = root / out["views_core_path_rel"]
        try:
            with open(path, encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader, None)
                out["views_core_rows"] = str(sum(1 for _r in reader)) if header else "0"
        except (OSError, ValueError, csv.Error) as ex:
            out["views_core_state"] = "unreadable"
            out["views_core_reason"] = _err(ex)
    return out


def _capture_status_counts(views):
    if not isinstance(views, list):
        return None
    counts = {}
    for v in views:
        status = v.get("capture_status") if isinstance(v, dict) else None
        key = "null" if status is None else str(status)
        counts[key] = counts.get(key, 0) + 1
    return counts


def run_row(run_dir, meta, root):
    """``(csv_row, json_objects)`` for one run folder."""
    run_dir, root = Path(run_dir), Path(root)
    row = dict((c, "") for c in COLUMNS)
    objs = dict((c, None) for c in JSON_COLUMNS)
    parent_rel = _rel(run_dir.parent, root) if run_dir != root else ""
    row.update(model_key=parent_rel, run_dir_rel=_rel(run_dir, root) or ".",
               run_dir_name=run_dir.name)
    revit = meta.get("revit_version")
    row.update({
        "run_id": _cell(meta.get("run_id")), "run_tag": _cell(meta.get("run_tag")),
        "as_of_date": _cell(meta.get("date")),
        "doc_title": _cell(meta.get("doc_title")), "doc_path": _cell(meta.get("doc_path")),
        "config_hash": _cell(meta.get("config_hash")),
        "git_commit": _cell(meta.get("git_commit")),
        "exporter_version": _cell(meta.get("exporter_version")),
        "revit_version_number": _cell(revit.get("version_number"))
        if isinstance(revit, dict) else "",
        "run_meta_schema": _cell(meta.get("schema")),
        "finalized": _cell(meta.get("finalized"))
        if isinstance(meta.get("finalized"), bool) else "",
    })
    requested, views = meta.get("views_requested"), meta.get("views")
    row["views_requested_count"] = str(len(requested)) if isinstance(requested, list) else ""
    row["views_count"] = str(len(views)) if isinstance(views, list) else ""
    objs["capture_status_counts"] = _capture_status_counts(views)

    rollup, rollup_objs = rollup_facts(run_dir, root)
    row.update(rollup)
    objs.update(rollup_objs)
    row.update(kinds_facts(run_dir, root))
    try:
        names = sorted(e.name for e in os.scandir(run_dir))
    except OSError as ex:
        names = _err(ex)
    row.update(dated_file_facts(run_dir, root, names))

    m = FOLDER_NAME.match(run_dir.name)
    folder_date, folder_run_id = m.group(1), m.group(2)
    row["chk_folder_date_eq_as_of"] = _bool_cell(
        folder_date == row["as_of_date"] if row["as_of_date"] else None)
    row["chk_folder_run_id_eq_run_meta"] = _bool_cell(
        folder_run_id == row["run_id"] if row["run_id"] else None)
    ids = objs["rollup_run_ids"]
    row["chk_rollup_run_id_eq_run_meta"] = _bool_cell(
        bool(ids) and set(ids) == {row["run_id"]}
        if row["run_id"] and ids is not None else None)
    a, b = row["kinds_class_map_sha256"], row["rollup_class_map_sha256"]
    row["chk_kinds_class_map_eq_rollup"] = _bool_cell(a == b if a and b else None)
    for c in JSON_COLUMNS:
        row[c] = "" if objs[c] is None else _json_cell(objs[c])
    return row, objs


def group_facts(rows):
    """The two across-row counts, filled in place."""
    same, hashes = {}, {}
    for r in rows:
        k = (r["model_key"], r["as_of_date"])
        same[k] = same.get(k, 0) + 1
        if r["config_hash"]:
            hashes.setdefault(r["model_key"], set()).add(r["config_hash"])
    for r in rows:
        r["n_runs_same_model_as_of"] = str(same[(r["model_key"], r["as_of_date"])])
        r["n_config_hash_in_model"] = str(len(hashes.get(r["model_key"], ())))


# --- output -------------------------------------------------------------------------

def _atomic_write(path, data):
    """``data`` (bytes) to ``path`` through a sibling temporary file."""
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def csv_bytes(rows):
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8")


def build_index(root, max_depth=4):
    """``(rows, json_rows, scan)``, rows sorted by (model_key, as_of_date, run_id)."""
    root = Path(root)
    found, scan = discover(root, max_depth)
    built = [run_row(run_dir, meta, root) for run_dir, meta in found]
    group_facts([r for r, _o in built])
    built.sort(key=lambda ro: (ro[0]["model_key"], ro[0]["as_of_date"], ro[0]["run_id"],
                               ro[0]["run_dir_rel"]))
    rows = [r for r, _o in built]
    json_rows = []
    for r, objs in built:
        jr = dict(r)
        jr.update(objs)
        json_rows.append(jr)
    return rows, json_rows, scan


def write_index(root, out_dir, rows, json_rows, scan):
    """Write the CSV, then the JSON naming its sha256 (written last)."""
    out_dir = Path(out_dir)
    csv_path, json_path = out_dir / CSV_NAME, out_dir / JSON_NAME
    _atomic_write(csv_path, csv_bytes(rows))
    record = {
        "schema": SCHEMA, "tool_version": TOOL_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": str(root),
        "columns": COLUMNS,
        "rows": json_rows,
        "scan": scan,
        "csv": CSV_NAME,
        "csv_sha256": sha256_file(csv_path),
    }
    _atomic_write(json_path, (json.dumps(record, indent=2, sort_keys=True) + "\n")
                  .encode("utf-8"))
    return csv_path, json_path


def run_folder_holding(path):
    """The nearest folder at or above ``path`` that holds ``run_meta.json``,
    or None. Checked on the path itself, not against the indexed rows: a run
    folder that discovery skipped (unreadable run_meta, beyond --max-depth,
    under a reserved name) is still a run folder the index must not write
    into."""
    path = Path(path).resolve()
    for folder in [path] + list(path.parents):
        if (folder / RUN_META_FILENAME).is_file():
            return folder
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="the export root to index")
    ap.add_argument("--out", default=None, help="output directory (default: --root)")
    ap.add_argument("--max-depth", type=int, default=4,
                    help="deepest folder searched for run folders (root = 0; default 4)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as ex:
        return 2 if ex.code else 0
    root = Path(args.root)
    if not root.is_dir():
        print("--root {0} is not a directory".format(root))
        return 2
    if args.max_depth < 0:
        print("--max-depth must be >= 0")
        return 2
    out = Path(args.out) if args.out else root
    holder = run_folder_holding(out)
    if holder is not None:
        print("--out {0} is inside the run folder {1}; the index never writes "
              "there".format(out, holder))
        return 2
    rows, json_rows, scan = build_index(root, args.max_depth)
    for r in rows:
        print("{0}  {1}  {2}  finalized={3}  rollup={4}  kinds={5}".format(
            r["model_key"] or ".", r["as_of_date"] or "-", r["run_id"] or "-",
            r["finalized"] or "-", r["rollup_state"], r["kinds_state"]))
    for s in scan["skipped"]:
        print("SKIPPED {0}: {1}".format(s["path_rel"], s["reason"]))
    try:
        out.mkdir(parents=True, exist_ok=True)
        csv_path, json_path = write_index(root, out, rows, json_rows, scan)
    except OSError as ex:
        print("could not write the index to {0}: {1}".format(out, _err(ex)))
        return 2
    print("wrote {0} and {1}".format(csv_path, json_path))
    if not rows:
        print("no run folder indexed under {0}".format(root))
        return 2
    every = all(r["finalized"] == "true" and r["rollup_state"] == "value" for r in rows)
    return 0 if every else 1


if __name__ == "__main__":
    sys.exit(main())
