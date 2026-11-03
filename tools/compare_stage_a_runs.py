"""Byte-identity comparison of two Stage A run directories (C3 gate 1).

Every file of run A is compared with the file of the same relative name in
run B. Binary files (the TIFFs) must be byte-identical. JSON and CSV files
are compared field by field, after masking each run's own run id and run
directory, and every differing field is reported as one of:

  * ALLOWED -- matches a rule in ALLOWED_JSON_RULES / ALLOWED_CSV_COLUMNS,
    each named: timing fields, wall clocks, the flag being compared, the
    loop-timing record's ``gated_steps``;
  * UNEXPECTED -- anything else, including a file present in one run only.

Nothing is dropped silently: ``--verbose`` prints the allowed fields too.

    python tools/compare_stage_a_runs.py <run_dir_a> <run_dir_b> [--verbose] [--json]

Exit: 0 no unexpected difference; 1 at least one; 2 a run directory could
not be read (no run_meta, or no run id). Standard library only.

tests/test_stage_a_legacy_prepass.py runs THIS comparison over the synthetic
flag-on / flag-off pair, so the test and the tool cannot disagree.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vop_interwoven import output_layout  # noqa: E402

# (rule name, regex over the generalised JSON path: list indices as "[]").
ALLOWED_JSON_RULES = (
    ("flag", r"^/config/stage_a_legacy_prepass$"),
    ("gated_steps", r"/loop_timing/pipeline/gated_steps(\[\])?$"),
    ("timing", r"(^|/)(timings_ms|loop_timing|process_memory|steps_ms|substeps_ms"
               r"|memory_tracker|run_summary)(/|\[|$)"),
    ("timing", r"(^|/)[A-Za-z0-9_]*(_ms|_sec|_s)$"),
    ("timing", r"(^|/)(elapsed_sec|elapsed_ms|ElapsedSec)$"),
    ("clock", r"(^|/)(generated_utc|captured_at|pinned_at|read_at_write|started_at"
              r"|finished_at|written_at|wall_time|saved_utc)$"),
    ("run_location", r"^/run_dir$"),
)
ALLOWED_CSV_COLUMNS = (("timing", "ElapsedSec"),)


def _flat(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            for item in _flat(v, path + "/" + str(k)):
                yield item
    elif isinstance(obj, list):
        if not obj:
            yield path, []
        for i, v in enumerate(obj):
            for item in _flat(v, "{0}[{1}]".format(path, i)):
                yield item
    else:
        yield path, obj


def run_identity(run_dir):
    """``(run_id, run_dir_name)`` from the run's own run_meta; raises."""
    path, why = output_layout.find_run_meta(run_dir)
    if path is None:
        raise ValueError("{0}: {1}".format(run_dir, why))
    with open(path) as handle:
        meta = json.load(handle)
    run_id = meta.get("run_id")
    if not run_id:
        raise ValueError("{0} carries no run_id".format(path))
    return str(run_id), os.path.basename(os.path.normpath(str(run_dir)))


def snapshot(run_dir):
    """``{relative path: bytes}`` for every file under ``run_dir``, the run's
    id, directory name and absolute path masked in JSON and CSV text."""
    run_id, dir_name = run_identity(run_dir)
    root = os.path.abspath(str(run_dir))
    masks = [(root.encode("utf-8"), b"<RUN_DIR>"),
             (json.dumps(root)[1:-1].encode("utf-8"), b"<RUN_DIR>"),
             (dir_name.encode("utf-8"), b"<RUN_DIR_NAME>"),
             (run_id.encode("utf-8"), b"<RUN_ID>")]
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as handle:
                data = handle.read()
            if name.endswith((".json", ".csv")):
                for old, new in masks:
                    data = data.replace(old, new)
            out[rel] = data
    return out


def _rule_for(general_path):
    for name, rx in ALLOWED_JSON_RULES:
        if re.search(rx, general_path):
            return name
    return None


def _json_fields(a, b):
    fa, fb = dict(_flat(json.loads(a.decode("utf-8")))), dict(_flat(json.loads(b.decode("utf-8"))))
    allowed, unexpected = {}, []
    missing = object()
    for path in sorted(set(fa) | set(fb)):
        if fa.get(path, missing) == fb.get(path, missing):
            continue
        rule = _rule_for(re.sub(r"\[\d+\]", "[]", path))
        if rule is None:
            unexpected.append(path)
        else:
            allowed.setdefault(rule, []).append(path)
    return allowed, unexpected


def _csv_fields(a, b):
    ra = list(csv.DictReader(io.StringIO(a.decode("utf-8"))))
    rb = list(csv.DictReader(io.StringIO(b.decode("utf-8"))))
    allowed, unexpected = {}, []
    if len(ra) != len(rb):
        return allowed, ["row count {0} != {1}".format(len(ra), len(rb))]
    rules = dict((col, name) for name, col in ALLOWED_CSV_COLUMNS)
    for i, (x, y) in enumerate(zip(ra, rb)):
        if list(x) != list(y):
            unexpected.append("header")
        for col in sorted(set(x) | set(y)):
            if x.get(col) != y.get(col):
                where = "row {0} {1}".format(i, col)
                if col in rules:
                    allowed.setdefault(rules[col], []).append(where)
                else:
                    unexpected.append(where)
    return allowed, unexpected


def compare(run_a, run_b):
    """PURE over the files. ``{"identical": [...], "allowed": {path: {rule:
    [fields]}}, "unexpected": {path: [fields or reason]}}``."""
    sa, sb = snapshot(run_a), snapshot(run_b)
    report = {"identical": [], "allowed": {}, "unexpected": {}}
    for path in sorted(set(sa) | set(sb)):
        if path not in sa or path not in sb:
            report["unexpected"][path] = ["present in run {0} only".format(
                "A" if path in sa else "B")]
            continue
        if sa[path] == sb[path]:
            report["identical"].append(path)
            continue
        if path.endswith(".json"):
            try:
                allowed, unexpected = _json_fields(sa[path], sb[path])
            except ValueError as ex:
                allowed, unexpected = {}, ["unparseable JSON: {0}".format(ex)]
        elif path.endswith(".csv"):
            allowed, unexpected = _csv_fields(sa[path], sb[path])
        else:
            allowed, unexpected = {}, ["bytes differ"]
        if allowed:
            report["allowed"][path] = allowed
        if unexpected:
            report["unexpected"][path] = unexpected
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run_a")
    parser.add_argument("run_b")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = compare(args.run_a, args.run_b)
    except (OSError, ValueError) as ex:
        print("comparison refused: {0}: {1}".format(type(ex).__name__, ex), file=sys.stderr)
        return 2
    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        return 1 if report["unexpected"] else 0
    print("identical files: {0}".format(len(report["identical"])))
    print("files with allowed differences only: {0}".format(
        len(set(report["allowed"]) - set(report["unexpected"]))))
    for path, by_rule in sorted(report["allowed"].items()):
        counts = ", ".join("{0} {1}".format(rule, len(f)) for rule, f in sorted(by_rule.items()))
        print("  ALLOWED  {0}: {1}".format(path, counts))
        if args.verbose:
            for rule, fields in sorted(by_rule.items()):
                for field in fields:
                    print("      [{0}] {1}".format(rule, field))
    print("files with UNEXPECTED differences: {0}".format(len(report["unexpected"])))
    for path, fields in sorted(report["unexpected"].items()):
        print("  UNEXPECTED  {0}".format(path))
        for field in fields[:50]:
            print("      {0}".format(field))
        if len(fields) > 50:
            print("      ... {0} more".format(len(fields) - 50))
    return 1 if report["unexpected"] else 0


if __name__ == "__main__":
    sys.exit(main())
