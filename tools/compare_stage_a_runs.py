"""Byte-identity comparison of two Stage A run directories (C3 gate 1).

Every file of run A is compared with the file of the same relative name in
run B. Binary files (the TIFFs) must be byte-identical. JSON and CSV files
are compared field by field, after masking each run's own run id and run
directory, and every differing field is reported as one of:

  * ALLOWED -- matches a rule in ALLOWED_JSON_RULES / ALLOWED_CSV_COLUMNS,
    each named: timing fields, wall clocks, the flag being compared
    (``stage_a_legacy_prepass`` or ``stage_a_gc_policy``), the loop-timing
    record's ``gated_steps``, ``gc_policy`` and ``gc_after_run.skipped``;
  * UNEXPECTED -- anything else, including a file present in one run only.

Two kinds of file are NOT capture output and are kept out of the verdict,
each listed in its own section, never dropped silently:

  * SKIPPED (DERIVED_FILES) -- written by the analysis tools AFTER the run
    (views_timing_<d>.*, the registration, grid, roll-up and kinds outputs),
    so whether they exist depends on what was run since, not on the capture;
  * DOCUMENT OPEN (view_fingerprints_<d>.json) -- read from the document
    BEFORE any view is captured. Two opens of one detached archive re-mint
    about 0.4 % of elements' VersionGuid (CLAUDE.md), so fingerprints may
    differ between runs that captured identically. Reported field by field;
    the exit code does not count them.

``--verbose`` prints the allowed fields too.

Two opt-ins for comparing runs made by DIFFERENT builds (2026-11-11, R1),
neither on by default:

  * ``--pixels`` -- a TIFF whose bytes differ is decoded (Pillow) and passes
    when its size, mode and every pixel are equal, reported ALLOWED as
    ``tiff_pixels_equal``. Without Pillow the comparison is REFUSED (exit 2),
    never silently byte-compared or skipped.
  * ``--allow RULE`` (repeatable), a name from OPT_IN_RULES:
    ``build`` -- what two builds stamp differently: the exporter version,
    run_meta's ``git_commit`` and ``exporter_source``;
    ``restore_bookkeeping`` -- the model sidecar's ``model_restore`` record
    (R1: whether the model members' override restore ran in the pass or was
    left to the registered capture's rollback);
    ``co_size_seed`` -- the ``co_sizing`` record, run_meta ``co_size_seed``
    and the views' diagnostics (S1: a seeded view captures in one attempt
    where the run before took two, and the first attempt's warnings go).
  * ``--grid`` (with ``--pixels``) -- a TIFF whose decoded pixels differ
    passes when the view's analysis grids agree by the grid rule
    (``grid_same`` / ``grid_boundary``; Greg accepted it for gate 1,
    2026-11-13). Both runs must be registered and gridded first.

Element ids the capture itself DRAWS (ticks, frame-bound lines; read from
each sidecar's ``registration_marks``) are relabelled by their recorded
order before fields are compared (``minted_id``): they are rolled back, and
their numbers depend only on how many elements the session minted before.

    python tools/compare_stage_a_runs.py <run_dir_a> <run_dir_b> [--verbose] [--json]
        [--pixels [--grid]] [--allow build --allow restore_bookkeeping ...]

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
    ("flag", r"^/config/(stage_a_legacy_prepass|stage_a_gc_policy)$"),
    # Which GC policy the pipeline ran (config.STAGE_A_GC_POLICIES); the
    # skipped collection's record says why it did not run.
    ("gc_policy", r"/loop_timing/pipeline/(gc_policy|gc_after_run/skipped)$"),
    ("gated_steps", r"/loop_timing/pipeline/gated_steps(\[\])?$"),
    ("timing", r"(^|/)(timings_ms|loop_timing|process_memory|steps_ms|substeps_ms"
               r"|phases_ms|memory_tracker|run_summary)(/|\[|$)"),
    # The N slowest elements of tick placement (stage_a_registered_capture,
    # AVOID_SLOWEST_N): ranked BY TIME, so which ids are listed is timing too.
    ("timing", r"/mark_avoidance/slowest(\[\]|/|$)"),
    # How many avoidance calls took longer than its threshold: a timing count.
    ("timing", r"/mark_avoidance/calls_over_threshold$"),
    ("timing", r"(^|/)[A-Za-z0-9_]*(_ms|_sec|_s)$"),
    ("timing", r"(^|/)ms$"),
    ("timing", r"(^|/)(elapsed_sec|elapsed_ms|ElapsedSec)$"),
    ("clock", r"(^|/)(generated_utc|captured_at|pinned_at|read_at_write|started_at"
              r"|finished_at|written_at|wall_time|saved_utc)$"),
    ("run_location", r"^/run_dir$"),
    # The operator's label for the run, as the run id is the clock's: the
    # dated file names carrying it are masked (_mask_file_date).
    ("run_label", r"^/run_tag$"),
)
ALLOWED_CSV_COLUMNS = (("timing", "ElapsedSec"),)

# Named allowances a caller must ASK for (--allow): each is a difference that
# is expected only when the two runs were made by different builds.
# name -> (JSON path regexes, CSV columns).
OPT_IN_RULES = {
    "build": ((r"(^|/)exporter_version$", r"^/git_commit(/|$)",
               r"^/exporter_source(/|$)"), ("ExporterVersion",)),
    "restore_bookkeeping": ((r"(^|/)model_restore(/|\[|$)",), ()),
    # A seeded view captures in ONE attempt where the run before took two:
    # its co_sizing record (attempts, first/seeded attempt, seed) and the
    # first attempt's own timing differ by design; its images must not.
    # ... and the discarded first attempt's own diagnostics (its overrun
    # warnings) are absent from a seeded run's views_diagnostics.
    "co_size_seed": ((r"(^|/)co_sizing(/|\[|$)", r"^/co_size_seed(/|$)",
                      r"^/views/[^/]+/diagnostics/(counts|events)(/|\[|$)"), ()),
}
TIFF_SUFFIXES = (".tif", ".tiff")

# Files the analysis tools write into a run AFTER it (not capture output):
# skipped, and listed as skipped. (rule name, regex over the relative path.)
DERIVED_FILES = (
    ("timing_report", r"^views_timing_[^/]*\.(csv|json)$"),
    ("registration", r"^(color_id|color_id_buffer)/[^/]*_anno\.(reg|registered)\.[^/]*$"),
    ("grid", r"^(grid|analysis_grid)/"),
    ("rollup", r"^grid_rollup[^/]*$"),
    ("kinds", r"^kinds_inventory[^/]*$"),
    ("decoded", r"\.decoded\.json$"),
)
# Read from the document before any capture: compared, reported apart.
DOCUMENT_OPEN_FILES = r"^view_fingerprints[^/]*\.json$"


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


def run_file_date(run_dir):
    """The ``<d>`` the run's dated files are named by (its tag, else its
    as-of date: output_layout.file_date), or None when it names none."""
    path, _why = output_layout.find_run_meta(run_dir)
    if path is None:
        return None
    with open(path) as handle:
        return output_layout.file_date(json.load(handle))


def _mask_file_date(rel, file_date):
    """A top-level dated file's name with the run's ``<d>`` masked, so two
    runs tagged differently (``run_meta_baseline_R.json`` /
    ``run_meta_R1.json``) are compared file to file rather than reported as
    present in one run only. Only ``_<d>.`` before the extension of a file in
    the run's root is replaced -- never a view's file under the capture
    folder, whose name is the view's."""
    if not file_date or "/" in rel:
        return rel
    stem, dot, ext = rel.rpartition(".")
    suffix = "_" + str(file_date)
    if dot and stem.endswith(suffix):
        return stem[:-len(suffix)] + "_<D>." + ext
    return rel


def snapshot(run_dir, mask_file_date=False):
    """``{relative path: bytes}`` for every file under ``run_dir``, the run's
    id, directory name and absolute path masked in JSON and CSV text. With
    ``mask_file_date`` (compare() sets it only when the two runs' file dates
    DIFFER, so a same-date pair keeps its real names in the report) the run's
    file date is masked too, in its dated top-level file names and where its
    records cite them."""
    run_id, dir_name = run_identity(run_dir)
    file_date = run_file_date(run_dir) if mask_file_date else None
    root = os.path.abspath(str(run_dir))
    masks = [(root.encode("utf-8"), b"<RUN_DIR>"),
             (json.dumps(root)[1:-1].encode("utf-8"), b"<RUN_DIR>"),
             (dir_name.encode("utf-8"), b"<RUN_DIR_NAME>"),
             (run_id.encode("utf-8"), b"<RUN_ID>")]
    if file_date:
        # The run's dated file names where its records cite them
        # (run_meta's view_fingerprints.file, views_diagnostics_compaction).
        masks.append((("_" + str(file_date) + ".").encode("utf-8"), b"_<D>."))
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if derived_rule(_mask_file_date(rel, file_date)) is not None:
                # Never compared (compare() lists it as SKIPPED): not read, so
                # a gridded run's registered TIFFs and arrays cost nothing.
                out[_mask_file_date(rel, file_date)] = None
                continue
            with open(full, "rb") as handle:
                data = handle.read()
            if name.endswith((".json", ".csv")):
                for old, new in masks:
                    data = data.replace(old, new)
            out[_mask_file_date(rel, file_date)] = data
    return out


def _rule_for(general_path, allow=()):
    for name, rx in ALLOWED_JSON_RULES:
        if re.search(rx, general_path):
            return name
    for name in allow:
        for rx in OPT_IN_RULES[name][0]:
            if re.search(rx, general_path):
                return name
    return None


def tiff_pixels_equal(a, b):
    """``(equal, reason)`` for two TIFFs' BYTES, decoded with Pillow: equal
    when size, mode and every pixel are. Raises ImportError without Pillow --
    the caller refuses rather than guessing. A run's own captures reach
    ~94 megapixels (P_OVERALL - LEVEL 1, 2026-11-16), over Pillow's
    decompression-bomb limit, so the limit is lifted for this read only."""
    from PIL import Image
    limit, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, None
    try:
        return _decoded_equal(Image, a, b)
    finally:
        Image.MAX_IMAGE_PIXELS = limit


def _decoded_equal(Image, a, b):
    with Image.open(io.BytesIO(a)) as ia, Image.open(io.BytesIO(b)) as ib:
        if ia.size != ib.size:
            return False, "decoded size {0} != {1}".format(ia.size, ib.size)
        if ia.mode != ib.mode:
            return False, "decoded mode {0} != {1}".format(ia.mode, ib.mode)
        if ia.tobytes() != ib.tobytes():
            return False, "decoded pixels differ"
    return True, None


def minted_ids(record):
    """The element ids a registered capture DREW in a sidecar (the ticks, the
    frame-bound lines), in their recorded order. They are rolled back with
    the capture, so their numbers say only how many elements the session had
    minted before: a run that skips attempts (S1's seeded views) shifts every
    later view's ticks. None of them names anything across runs."""
    marks = (record or {}).get("registration_marks") if isinstance(record, dict) else None
    out = []
    for key, sub in (("marks", None), ("frame_bounds", "created")):
        items = (marks or {}).get(key)
        if sub is not None:
            items = (items or {}).get(sub) if isinstance(items, dict) else None
        for item in items or []:
            mid = item.get("id") if isinstance(item, dict) else None
            if isinstance(mid, int) and not isinstance(mid, bool):
                out.append(mid)
    return out


def _relabel(obj, labels):
    """``obj`` with every minted id -- as a dict key or an int value -- put
    as its label."""
    if isinstance(obj, dict):
        return dict((labels.get(k, k), _relabel(v, labels)) for k, v in obj.items())
    if isinstance(obj, list):
        return [_relabel(v, labels) for v in obj]
    if isinstance(obj, int) and not isinstance(obj, bool) and str(obj) in labels:
        return labels[str(obj)]
    return obj


def _json_fields(a, b, allow=()):
    ja, jb = json.loads(a.decode("utf-8")), json.loads(b.decode("utf-8"))
    ma, mb = minted_ids(ja), minted_ids(jb)
    relabelled = None
    if ma != mb and len(ma) == len(mb):
        ja = _relabel(ja, dict((str(i), "<minted {0}>".format(n)) for n, i in enumerate(ma)))
        jb = _relabel(jb, dict((str(i), "<minted {0}>".format(n)) for n, i in enumerate(mb)))
        relabelled = "{0} element id(s) drawn by the capture, relabelled by their " \
                     "recorded order".format(len(ma))
    fa, fb = dict(_flat(ja)), dict(_flat(jb))
    allowed, unexpected = {}, []
    if relabelled:
        allowed["minted_id"] = [relabelled]
    missing = object()
    for path in sorted(set(fa) | set(fb)):
        if fa.get(path, missing) == fb.get(path, missing):
            continue
        rule = _rule_for(re.sub(r"\[\d+\]", "[]", path), allow)
        if rule is None:
            unexpected.append(path)
        else:
            allowed.setdefault(rule, []).append(path)
    return allowed, unexpected


def _csv_fields(a, b, allow=()):
    ra = list(csv.DictReader(io.StringIO(a.decode("utf-8"))))
    rb = list(csv.DictReader(io.StringIO(b.decode("utf-8"))))
    allowed, unexpected = {}, []
    if len(ra) != len(rb):
        return allowed, ["row count {0} != {1}".format(len(ra), len(rb))]
    rules = dict((col, name) for name, col in ALLOWED_CSV_COLUMNS)
    for name in allow:
        for col in OPT_IN_RULES[name][1]:
            rules[col] = name
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


def derived_rule(path):
    """The DERIVED_FILES rule ``path`` matches, or None."""
    for name, rx in DERIVED_FILES:
        if re.search(rx, path):
            return name
    return None


_VIEW_ID_IN_TIFF = re.compile(r"_(\d+)(_anno)?\.tiff?$", re.IGNORECASE)


def _grid_dir(run_dir):
    for name in (output_layout.GRID_DIR[output_layout.V2],
                 output_layout.GRID_DIR[output_layout.V1]):
        path = os.path.join(str(run_dir), name)
        if os.path.isdir(path):
            return path
    return None


def grid_verdict(run_a, run_b, path, pixel_reason):
    """``(allowed, unexpected)`` for a TIFF whose decoded pixels differ, by
    the grid rule (docs/DESIGN_CHANGE_SKIP.md, "What counts as the capture
    differs"; tools/validate_change_skip.compare_grid): the view's two
    analysis grids agree, or differ only by boundary noise -> ALLOWED
    (``grid_same`` / ``grid_boundary``). A grid that differs, is missing
    (run tools/register_stage_a_annotation.py and tools/stage_a_grid.py on
    both runs first) or cannot be compared stays UNEXPECTED, with why."""
    from tools import validate_change_skip as vcs
    m = _VIEW_ID_IN_TIFF.search(os.path.basename(path))
    if m is None:
        return {}, ["{0}; no view id in the file name for the grid rule".format(pixel_reason)]
    grids = (_grid_dir(run_a), _grid_dir(run_b))
    if None in grids:
        return {}, ["{0}; no grid in run {1} (register and grid both runs for "
                    "--grid)".format(pixel_reason, "A" if grids[0] is None else "B")]
    differs, record = vcs.compare_grid(int(m.group(1)), grids[0], grids[1])
    if differs is None:
        return {}, ["{0}; grid rule undecided: {1}".format(pixel_reason, record.get("reason"))]
    summary = ("grid rows +{0}/-{1}, cells differ {2}, one-sided max {3} px".format(
        record["rows_added"], record["rows_removed"], record["cells_differ"],
        record["one_sided_max_px"]))
    if differs:
        return {}, ["{0}; GRID DIFFERS: {1}".format(pixel_reason, summary)]
    rule = "grid_boundary" if record.get("boundary_only") else "grid_same"
    return {rule: ["{0}; {1}".format(pixel_reason, summary)]}, []


def compare(run_a, run_b, pixels=False, allow=(), grid=False):
    """PURE over the files. ``{"identical": [...], "allowed": {path: {rule:
    [fields]}}, "unexpected": {path: [fields or reason]}, "skipped": {path:
    rule}, "document_open": {path: [fields]}}``. Only ``unexpected`` is a
    difference in what was captured. ``pixels``, ``allow`` and ``grid``: the
    module docstring's opt-ins (``grid`` needs ``pixels``); an unknown
    ``allow`` name raises ValueError."""
    if grid and not pixels:
        raise ValueError("--grid judges TIFFs whose decoded pixels differ: it needs --pixels")
    unknown = sorted(set(allow) - set(OPT_IN_RULES))
    if unknown:
        raise ValueError("unknown --allow rule(s) {0}; known: {1}".format(
            unknown, sorted(OPT_IN_RULES)))
    if pixels:
        import PIL  # noqa: F401 -- ImportError is the caller's refusal
    mask = run_file_date(run_a) != run_file_date(run_b)
    sa, sb = snapshot(run_a, mask), snapshot(run_b, mask)
    report = {"identical": [], "allowed": {}, "unexpected": {}, "skipped": {},
              "document_open": {}}
    for path in sorted(set(sa) | set(sb)):
        rule = derived_rule(path)
        if rule is not None:
            report["skipped"][path] = rule
            continue
        if re.search(DOCUMENT_OPEN_FILES, path):
            if path not in sa or path not in sb:
                report["document_open"][path] = ["present in run {0} only".format(
                    "A" if path in sa else "B")]
            elif sa[path] != sb[path]:
                try:
                    allowed, fields = _json_fields(sa[path], sb[path])
                except ValueError as ex:
                    allowed, fields = {}, ["unparseable JSON: {0}".format(ex)]
                if fields:
                    report["document_open"][path] = fields
                if allowed:
                    report["allowed"][path] = allowed
            else:
                report["identical"].append(path)
            continue
        if path not in sa or path not in sb:
            report["unexpected"][path] = ["present in run {0} only".format(
                "A" if path in sa else "B")]
            continue
        if sa[path] == sb[path]:
            report["identical"].append(path)
            continue
        if path.endswith(".json"):
            try:
                allowed, unexpected = _json_fields(sa[path], sb[path], allow)
            except ValueError as ex:
                allowed, unexpected = {}, ["unparseable JSON: {0}".format(ex)]
        elif path.endswith(".csv"):
            allowed, unexpected = _csv_fields(sa[path], sb[path], allow)
        elif pixels and path.lower().endswith(TIFF_SUFFIXES):
            try:
                equal, why = tiff_pixels_equal(sa[path], sb[path])
            except (OSError, ValueError) as ex:
                equal, why = False, "undecodable TIFF: {0}: {1}".format(
                    type(ex).__name__, ex)
            if equal:
                allowed, unexpected = {"tiff_pixels_equal": [
                    "bytes differ; size, mode and every pixel equal"]}, []
            elif grid:
                allowed, unexpected = grid_verdict(run_a, run_b, path, why)
            else:
                allowed, unexpected = {}, [why]
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
    parser.add_argument("--pixels", action="store_true",
                        help="compare TIFFs whose bytes differ by decoded pixels")
    parser.add_argument("--grid", action="store_true",
                        help="judge TIFFs whose pixels differ by the analysis-grid rule "
                             "(register and grid both runs first)")
    parser.add_argument("--allow", action="append", default=[],
                        choices=sorted(OPT_IN_RULES),
                        help="an opt-in allowance (repeatable)")
    args = parser.parse_args(argv)
    try:
        report = compare(args.run_a, args.run_b, pixels=args.pixels, allow=args.allow,
                         grid=args.grid)
    except ImportError as ex:
        print("comparison refused: --pixels needs Pillow ({0})".format(ex), file=sys.stderr)
        return 2
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
    print("derived files skipped (written after the run, not capture output): {0}".format(
        len(report["skipped"])))
    for path, rule in sorted(report["skipped"].items()):
        print("  SKIPPED  {0} ({1})".format(path, rule))
    for path, fields in sorted(report["document_open"].items()):
        views = sorted(set(f.split("/")[2] for f in fields
                           if f.startswith("/views/") and f.endswith("/fingerprint")))
        print("  DOCUMENT OPEN  {0}: {1} field(s) differ; fingerprint changed for {2} "
              "view(s) -- not capture output (detached opens re-mint VersionGuids)".format(
                  path, len(fields),
                  sum(1 for f in fields if f.endswith("/fingerprint"))))
        if args.verbose:
            for field in fields:
                print("      {0}".format(field))
        elif views:
            print("      views: {0}".format(", ".join(views)))
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
