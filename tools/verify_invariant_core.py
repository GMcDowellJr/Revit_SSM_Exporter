#!/usr/bin/env python3
"""Path-independent verification invariant core for VOP run bundles.

WHAT THIS IS
------------
A versioned tool that reads a run bundle (the ``views_core`` / ``views_vop`` /
``views_perf`` / ``views_occlusion`` CSVs) and emits a single JSON verification
bundle carrying eight numbered invariants, a layered summary (L0-L4) and a
violation register.

It is deliberately NOT a notebook and NOT a hand-assembled export: a summary
produced by hand cannot be re-derived, so nothing downstream of it is
verifiable. The invariants here are PATH-INDEPENDENT -- they read only
quantities that both the color-ID path and the geometry path emit -- so they
survive the classification-surface deletion untouched, which is the reason
this exists before that deletion rather than after it.

WHAT IT IS NOT
--------------
It is not a pass/fail gate. The output is a REGISTER. The process exit code
reports whether the tool could DECIDE, not whether the data was good:

    0   a bundle was produced
    1   only with --fail-on-new, and only when a violation fired that the
        known-open baseline does not already carry
    2   REFUSAL -- the tool could not identify something it needs, so it
        declines rather than certifying a reading it cannot stand behind

Exit 1 is opt-in precisely so that the default behaviour cannot drift into
being read as a verdict.

THE REFUSALS, AND WHY THEY ARE THE POINT
----------------------------------------
This repository has a recorded history of checks that accumulated properties
until no attack came to mind, and were then defeated by a concrete one. The
lesson recorded there is that a validator earns its name by DECLINING the
cases it cannot decide. So:

  - A required CSV role that is missing, or that matches more than one file,
    is a refusal. It is not resolved by picking the newest.
  - An empty scan is a refusal. ``glob`` on a mistyped directory returns
    nothing and raises nothing, and a bundle certifying zero rows as clean is
    the exact failure this repository already shipped once.
  - A boolean column carrying a token outside the declared vocabulary is a
    refusal. It is NOT coerced, because ``bool("False")`` is True and a
    silently-inverted sheet flag would make invariant 3 report the opposite
    of the truth.
  - A numeric column carrying an unparseable non-empty value is a refusal.
  - An unsupported bundle schema version is a refusal on read.

An invariant that cannot be evaluated because a COLUMN it needs is absent is
reported as ``NOT_EVALUABLE`` with the reason, not silently skipped and not
counted as holding. That is refusal at invariant granularity.

DECLARED CONSTANTS, AND THE DATA THAT EXERCISES THEM
-----------------------------------------------------
``N_MIN_ORDER_STATISTIC`` is the only threshold in this file. It is a
REPORTING rule, not a tolerance: nothing is compared against it to decide
whether a value is acceptable. See its own comment.

No tolerance, floor or epsilon is introduced anywhere else. In particular
invariant 7 compares two cell totals and asserts NOTHING about their
agreement, because the two quantities count different populations by
construction and a tolerance would launder that definitional gap into a
number that looks like a measurement.

PATH
----
``path`` (color-id / geometry) is a property of the BUNDLE, not of a row.
No CSV column discriminates it: ``RowSource`` is a hardcoded constant
("vop_interwoven") written unconditionally by both paths, so it cannot. The
operator declares it with --path and it is recorded in L0. Within one bundle
the invariant-2 key ``(RunId, ViewId, path)`` therefore reduces to
``(RunId, ViewId)``.

BUNDLE GRAVITY
--------------
L0 records every input file, its sha256, its byte count and every row that
was dropped and why. A bundle labelled "reviewable summary" drifts into being
treated as the run itself, at which point absence from the bundle reads as
absence from the data. L0 is what makes this bundle self-describing as lossy.
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import math
import os
import re
import sys
from collections import defaultdict

BUNDLE_SCHEMA_VERSION = "1.0"
SUPPORTED_BUNDLE_SCHEMA_VERSIONS = {"1.0"}

TOOL_NAME = "verify_invariant_core"

# WHY 8, and WHY ONLY FOR ORDER STATISTICS.
#
# The value is lifted from MIN_N_FOR_SUMMARY in
# tools/notes/g2_bbox_excursion_report.py, whose recorded rationale is an
# order-statistic argument: at n >= 8 at least three observations sit strictly
# on each side of the median, so neither a single value nor a pair can carry
# it, and it is the smallest n for which both quartiles land at interior
# ranks (2.25 and 6.75).
#
# That rationale justifies a floor for MEDIANS AND QUANTILES and for nothing
# else. A count is not an order statistic: "n=1" is small, not misleading, and
# suppressing it would hide the very sparseness the floor exists to flag. So
# counts and ratios are ALWAYS emitted, with n alongside, at any n; only
# median/p10/p90 are withheld below the floor.
#
# This is a REPORTING RULE, not a tolerance. No measurement changes when it
# changes. Withheld statistics are emitted as an explicit suppression record
# carrying the reason -- never omitted, because an absent key and a withheld
# key read identically to a consumer.
N_MIN_ORDER_STATISTIC = 8

# The same floor governs the L2 rollup: a (ViewType x Category) cell that
# cannot carry a summary is exactly the cell that should roll up. Reusing it
# introduces no second threshold and therefore no second thing to justify.
N_MIN_L2_ROLLUP = N_MIN_ORDER_STATISTIC

# Distinct from the AnnoCells_OTHER category, which is a real annotation
# bucket emitted by the pipeline. Two different things must not share a name.
L2_ROLLUP_KEY = "OTHER_ROLLUP"

# ViewType is written as "" when its lookup failed (csv_export.py sets it in
# the exception path). "" is not a view type; label it so a reader cannot
# mistake the group for a real one.
VIEWTYPE_UNSET = "__UNSET__"

ROLE_PATTERNS = {
    "views_core": "views_core_*.csv",
    "views_vop": "views_vop_*.csv",
    "views_perf": "views_perf_*.csv",
    "views_occlusion": "views_occlusion_*.csv",
}

# core/vop/perf are required because invariants 2-8 read columns from them.
# views_occlusion is recorded (checksum, row count, its RunIds feed invariant
# 1) but not required: no invariant here consumes it, and refusing a bundle
# for the absence of a file nothing reads would be a refusal that teaches
# nothing. Its absence is stated in L0 rather than assumed.
REQUIRED_ROLES = ("views_core", "views_vop", "views_perf")
OPTIONAL_ROLES = ("views_occlusion",)

ANNO_CATEGORIES = ("TEXT", "TAG", "DIM", "DETAIL", "LINES", "REGION", "OTHER")

CACHE_MODES = ("enabled", "disabled", "unknown")
PATHS = ("color-id", "geometry")

_TRUE_TOKENS = frozenset(["true", "t", "y", "yes", "1"])
_FALSE_TOKENS = frozenset(["false", "f", "n", "no", "0"])

STATUS_HOLDS = "HOLDS"
STATUS_VIOLATED = "VIOLATED"
STATUS_NOT_EXERCISED = "NOT_EXERCISED"
STATUS_NOT_EVALUABLE = "NOT_EVALUABLE"
STATUS_REPORT_ONLY = "REPORT_ONLY"

# Integer syntax, matched BEFORE any float conversion. Routing an identifier
# through float() silently collapses values above 2**53 --
# 9007199254740992 and ...93 both become ...92 -- which would merge two
# distinct views into one key, fabricate duplicate-key findings and corrupt
# every join. This repository reads Revit 2025's 64-bit ElementId.Value
# (thinrunner_streaming.py), so those magnitudes are reachable, not theoretical.
_INTEGER_TEXT = re.compile(r"^[+-]?[0-9]+$")


class Refusal(Exception):
    """The tool cannot identify something it needs, so it declines.

    Every Refusal message must say what would have to change for the tool to
    proceed. A refusal that only reports a failure teaches the reader nothing
    about how to resolve it.
    """


# ---------------------------------------------------------------------------
# Known-open baseline
# ---------------------------------------------------------------------------
#
# Seeded from run 20260917T112500 (geometry path).
#
# THAT RUN IS DIRTY. What is seeded here is the EXISTENCE of each violation,
# never its values. The observed figures are recorded in `observed_dirty` as a
# historical note and must not be asserted against, compared to, or used as a
# prior. They are an example of the data type.
#
# The register's job is to make the post-deletion read "new violations only".
# Without a baseline, collateral damage from the classification deletion would
# land underneath these pre-existing failures and be invisible.
#
# `detector` records whether THIS tool version can observe the violation.
# An entry with detector None is carried forward and reported as
# KNOWN_OPEN_UNDETECTED: its absence from a run's findings is NOT evidence
# that it was fixed. Claiming otherwise is the same error as certifying an
# empty scan.
KNOWN_OPEN_BASELINE = {
    "I7.CELL_TOTAL_DISAGREEMENT": {
        "first_observed_run": "20260917T112500",
        "detector": "I7",
        "detector_mode": "REPORT_ONLY_NO_ASSERTION",
        "summary": (
            "FilledCells (views_perf) and ModelOnly+Overlap+AnnoOnly "
            "(views_vop) disagree for the same view."
        ),
        "why_not_asserted": (
            "The two totals count different populations by construction. "
            "The fix is a declared definition of what each counts, not a "
            "tolerance. Out of scope for this unit."
        ),
        "observed_dirty": [
            [2708, 2708], [8849, 8908], [5778, 6080],
            [1521, 1768], [1530, 1942],
        ],
    },
    "I8.VIEW_FRAME_HASH_COLLISION": {
        "first_observed_run": "20260917T112500",
        "detector": "I8",
        "detector_mode": "ACTIVE",
        "summary": (
            "One ViewFrameHash covers more than one distinct "
            "(ViewId, grid dims) key."
        ),
        "observed_dirty": [
            {"hash": "1c1b4f43", "view_ids": [542078, 554620]},
            {"hash": "6147c316", "view_ids": [770742, 630733],
             "grid_dims": [[52, 152], [97, 25]],
             "note": "7904 cells vs 2425 cells; latent in that run, not active"},
        ],
    },
    "I2.DUPLICATE_VIEW_ROW": {
        "first_observed_run": "20260917T112500",
        "detector": "I2",
        "detector_mode": "ACTIVE",
        "summary": (
            "A view emitted more than one row in a single run (one fresh, "
            "one cached)."
        ),
        "observed_dirty": {"views_double_processed": 4, "views_total": 8},
    },
    "I2.CACHE_REHYDRATION_PROVENANCE": {
        "first_observed_run": "20260917T112500",
        "detector": None,
        "detector_mode": "NO_DETECTOR",
        "summary": (
            "A FromCache row carries mixed provenance: ResolutionMode "
            "adaptive->canonical, CapTriggered True->False, "
            "CellSizeRequested rewritten to equal Effective, element and "
            "timing counts zeroed -- while CollectMs / RasterGeomExtractMs / "
            "RasterCellWriteMs copy through verbatim."
        ),
        "why_no_detector": (
            "Invariant 2 DROPS FromCache rows, so they leave the population "
            "before any comparison. Detecting this needs a fresh-vs-cached "
            "row comparison that the cache redesign (7.1) may make moot. "
            "Carried here so its absence from findings is never read as a fix."
        ),
    },
}


# ---------------------------------------------------------------------------
# Parsing primitives -- each refuses rather than coercing
# ---------------------------------------------------------------------------

def parse_tribool(raw, column, role, row_index):
    """True / False / None, or refuse.

    None means the cell was empty: UNPOPULATED, which is a third state and
    not False. Collapsing it to False is how "never written" becomes
    indistinguishable from "written as false", which is the exact ambiguity
    invariant 3's NOT_EXERCISED state exists to name.

    Anything outside the declared vocabulary refuses. It is not truthiness-
    tested: bool("False") is True, and a sheet flag read that way would
    invert invariant 3's antecedent without any symptom.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    lowered = text.lower()
    if lowered in _TRUE_TOKENS:
        return True
    if lowered in _FALSE_TOKENS:
        return False
    raise Refusal(
        "{0} row {1}: column {2} carries {3!r}, which is not in the declared "
        "boolean vocabulary (true/t/y/yes/1, false/f/n/no/0, or empty for "
        "unpopulated). To proceed, either correct the emitting code or add "
        "the token to _TRUE_TOKENS/_FALSE_TOKENS here -- deliberately, "
        "because a wrong addition silently inverts invariant 3.".format(
            role, row_index, column, text)
    )


def parse_number(raw, column, role, row_index):
    """float or None, or refuse. Empty means absent, not zero."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    try:
        value = float(text)
    except ValueError:
        raise Refusal(
            "{0} row {1}: column {2} carries {3!r}, which is not a number and "
            "is not empty. To proceed, correct the emitting code; this tool "
            "will not substitute a value for it.".format(
                role, row_index, column, text)
        )
    if not math.isfinite(value):
        # float() accepts "nan", "inf" and an overflowing exponent like
        # "1e9999" without complaint. Letting one through costs twice: a
        # non-finite float reaches json.dumps as the non-standard tokens NaN /
        # Infinity, which strict consumers reject, and an integer field such
        # as Width reaches int() and raises ValueError or OverflowError --
        # a traceback and exit 1, not the exit 2 this tool documents for
        # every input it declines.
        raise Refusal(
            "{0} row {1}: column {2} carries {3!r}, which parses as a "
            "non-finite float ({4}). A measurement is finite; this is an "
            "emitter defect, not a value to carry. To proceed, correct the "
            "emitting code.".format(role, row_index, column, text, value)
        )
    return value


def parse_int(raw, column, role, row_index):
    if raw is not None:
        text = str(raw).strip()
        if _INTEGER_TEXT.match(text):
            # Exact. Python ints are arbitrary precision; the float path below
            # is only for a value written in float form, such as "80.0".
            return int(text)
    value = parse_number(raw, column, role, row_index)
    if value is None:
        return None
    if value != int(value):
        raise Refusal(
            "{0} row {1}: column {2} carries {3!r}, which is not an integer. "
            "Grid dimensions and cell counts are counts; a fractional one "
            "means the emitter is wrong.".format(role, row_index, column, raw)
        )
    return int(value)


def _round6(value):
    if value is None:
        return None
    return round(float(value), 6)


def _ratio(numerator, denominator, label="ratio"):
    """numerator/denominator, or None when it is not defined.

    None -- never 0.0, never 1.0 -- when the denominator is zero or either
    side is absent. A substituted ratio is a fabricated measurement.

    Refuses when two individually finite operands produce a non-finite
    quotient (1e308 / 1e-308 overflows). Refusing non-finite INPUTS is not
    enough: a derived value reaches json.dumps just the same, and emits the
    non-standard token Infinity, which breaks the strict-JSON contract this
    bundle claims. Derived arithmetic has to be validated where it is
    produced, not only where it is read.
    """
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    value = float(numerator) / float(denominator)
    if not math.isfinite(value):
        raise Refusal(
            "{0}: {1} / {2} is not finite ({3}). Both operands are finite, so "
            "this is an overflow in the quotient, and emitting it would put a "
            "non-standard JSON token in the bundle. Correct the emitting code; "
            "this tool will not carry a value it cannot represent.".format(
                label, numerator, denominator, value)
        )
    return _round6(value)


# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------

def resolve_roles(bundle_dir):
    """Map each role to exactly one file, or refuse.

    More than one match is a refusal rather than a newest-wins pick: two
    ``views_core_*.csv`` in one directory means the bundle's identity is
    ambiguous, and silently choosing one certifies a run the operator did not
    name.
    """
    if not os.path.isdir(bundle_dir):
        raise Refusal(
            "bundle directory {0!r} does not exist. glob() on a missing "
            "directory yields nothing and raises nothing, so proceeding would "
            "certify a scan of zero files.".format(bundle_dir)
        )
    resolved = {}
    for role in REQUIRED_ROLES + OPTIONAL_ROLES:
        matches = sorted(glob.glob(os.path.join(bundle_dir, ROLE_PATTERNS[role])))
        if len(matches) > 1:
            raise Refusal(
                "role {0} matches {1} files in {2}: {3}. Exactly one is "
                "required. Separate the runs into their own directories."
                .format(role, len(matches), bundle_dir,
                        ", ".join(os.path.basename(m) for m in matches))
            )
        if not matches:
            if role in REQUIRED_ROLES:
                raise Refusal(
                    "role {0} ({1}) is missing from {2}. Invariants 2-8 read "
                    "columns from it; there is no reading of this bundle that "
                    "does not need it.".format(
                        role, ROLE_PATTERNS[role], bundle_dir)
                )
            resolved[role] = None
            continue
        resolved[role] = matches[0]
    return resolved


def _sha256_and_size(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def read_role(path, role):
    """Return (fieldnames, rows). Refuses on an unreadable or empty file."""
    with open(path, "r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    if not fieldnames:
        raise Refusal(
            "{0} ({1}) has no header row. An unheadered CSV cannot be read by "
            "column, and reading it positionally would bind this tool to a "
            "column order nothing guarantees.".format(role, path)
        )
    if not rows:
        raise Refusal(
            "{0} ({1}) has a header but zero data rows. A bundle built over "
            "zero rows would report every invariant clean for the reason that "
            "nothing was examined.".format(role, path)
        )
    return list(fieldnames), rows


# ---------------------------------------------------------------------------
# Summary statistics -- invariant 5
# ---------------------------------------------------------------------------

def summarize(values, label):
    """Counts and range always; order statistics only at n >= the floor.

    The return value ALWAYS carries n, and always carries either
    ``order_statistics`` or ``suppressed``. It never omits both, because to a
    consumer an absent key and a withheld key look the same, and the whole
    point of invariant 5 is that a withheld statistic must be visible AS
    withheld.
    """
    present = [v for v in values if v is not None]
    result = {
        "label": label,
        "n": len(present),
        "n_missing": len(values) - len(present),
        "n_min_order_statistic": N_MIN_ORDER_STATISTIC,
    }
    if not present:
        result["suppressed"] = {
            "reason": "no non-null observations",
            "n": 0,
        }
        return result
    ordered = sorted(present)
    # min/max are extremes, not order statistics in the sense the floor
    # governs -- they are the observed range and are reported at any n.
    result["min"] = _round6(ordered[0])
    result["max"] = _round6(ordered[-1])
    if len(ordered) < N_MIN_ORDER_STATISTIC:
        result["suppressed"] = {
            "reason": "n={0} < n_min={1}; median and quantiles withheld".format(
                len(ordered), N_MIN_ORDER_STATISTIC),
            "n": len(ordered),
        }
        return result
    result["order_statistics"] = {
        "median": _round6(_quantile(ordered, 0.5)),
        "p10": _round6(_quantile(ordered, 0.10)),
        "p90": _round6(_quantile(ordered, 0.90)),
    }
    return result


def _quantile(ordered, q):
    """Linear-interpolation quantile over an already-sorted, non-empty list."""
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


# ---------------------------------------------------------------------------
# Bundle loading
# ---------------------------------------------------------------------------

def load_bundle(resolved):
    loaded = {}
    for role, path in resolved.items():
        if path is None:
            loaded[role] = None
            continue
        sha256, size = _sha256_and_size(path)
        fieldnames, rows = read_role(path, role)
        loaded[role] = {
            "role": role,
            "path": path,
            "basename": os.path.basename(path),
            "sha256": sha256,
            "bytes": size,
            "fieldnames": fieldnames,
            "rows": rows,
        }
    return loaded


def _require_key(row, column, role, row_index):
    raw = row.get(column)
    text = "" if raw is None else str(raw).strip()
    if text == "":
        raise Refusal(
            "{0} row {1}: {2} is empty. A row with no {2} cannot be keyed, so "
            "it can be neither deduplicated nor joined, and excluding it "
            "silently would shrink every population below without saying so."
            .format(role, row_index, column)
        )
    return text


def key_rows(role_data):
    """Attach a (RunId, ViewId) key to each row. Refuses on an unkeyable row."""
    if role_data is None:
        return []
    keyed = []
    for index, row in enumerate(role_data["rows"], start=1):
        run_id = _require_key(row, "RunId", role_data["role"], index)
        view_id_text = _require_key(row, "ViewId", role_data["role"], index)
        view_id = parse_int(view_id_text, "ViewId", role_data["role"], index)
        keyed.append({
            "row_index": index,
            "run_id": run_id,
            "view_id": view_id,
            "raw": row,
        })
    return keyed


# ---------------------------------------------------------------------------
# I1 -- single RunId per bundle
# ---------------------------------------------------------------------------

def invariant_1_single_run_id(keyed_by_role):
    per_role = {}
    everything = set()
    for role, keyed in keyed_by_role.items():
        if not keyed:
            per_role[role] = None
            continue
        run_ids = sorted({entry["run_id"] for entry in keyed})
        per_role[role] = run_ids
        everything.update(run_ids)
    distinct = sorted(everything)
    result = {
        "id": "I1",
        "name": "single RunId per bundle",
        "per_role_run_ids": per_role,
        "distinct_run_ids": distinct,
    }
    if len(distinct) == 1:
        result["status"] = STATUS_HOLDS
        result["run_id"] = distinct[0]
        return result, []
    result["status"] = STATUS_VIOLATED
    finding = {
        "violation_id": "I1.MULTIPLE_RUN_IDS",
        "invariant": "I1",
        "summary": "bundle carries {0} distinct RunIds".format(len(distinct)),
        "detail": {"run_ids": distinct, "per_role": per_role},
    }
    return result, [finding]


# ---------------------------------------------------------------------------
# I2 -- one row per (RunId, ViewId); FromCache dropped and COUNTED
# ---------------------------------------------------------------------------

def invariant_2_dedupe(loaded, keyed_by_role, cache_mode):
    per_role = {}
    findings = []
    retained_by_role = {}
    for role in REQUIRED_ROLES + OPTIONAL_ROLES:
        role_data = loaded.get(role)
        keyed = keyed_by_role.get(role) or []
        if role_data is None:
            per_role[role] = {"status": STATUS_NOT_EVALUABLE,
                              "reason": "role absent from bundle"}
            retained_by_role[role] = []
            continue
        has_from_cache = "FromCache" in role_data["fieldnames"]
        dropped = []
        retained = []
        for entry in keyed:
            if not has_from_cache:
                retained.append(entry)
                continue
            flag = parse_tribool(entry["raw"].get("FromCache"), "FromCache",
                                 role, entry["row_index"])
            if flag is True:
                dropped.append(entry)
            else:
                # None (unpopulated) is retained, not dropped: dropping on an
                # unwritten flag would silently shrink the population.
                retained.append(entry)
        retained_by_role[role] = retained

        # Counted over EVERY keyed row, before the cache filter -- not over
        # the retained ones. A view emitted twice as one fresh row plus one
        # cached row is the double-processing the baseline seeds, and it is
        # precisely the shape the filter erases: drop the cached row first and
        # exactly one row remains, so the duplication reports HOLDS and the
        # register then states that its ACTIVE detector ran and did not fire.
        # A detector that cannot see its own baselined scenario is worse than
        # no detector, because its silence is read as evidence.
        emitted = defaultdict(list)
        for entry in keyed:
            emitted[(entry["run_id"], entry["view_id"])].append(entry)
        dropped_indices = {e["row_index"] for e in dropped}
        duplicates = {}
        duplicate_view_ids = []
        for key, entries in emitted.items():
            if len(entries) > 1:
                duplicates["{0}|{1}".format(key[0], key[1])] = {
                    "row_indices": [e["row_index"] for e in entries],
                    "from_cache": [e["row_index"] in dropped_indices
                                   for e in entries],
                }
                duplicate_view_ids.append(key[1])
        counts = defaultdict(list)
        for entry in retained:
            counts[(entry["run_id"], entry["view_id"])].append(entry["row_index"])

        record = {
            "rows_read": len(keyed),
            "from_cache_column_present": has_from_cache,
            "rows_dropped_from_cache": len(dropped) if has_from_cache else None,
            "rows_retained": len(retained),
            "distinct_keys_retained": len(counts),
            "distinct_keys_emitted": len(emitted),
            "duplicate_keys": duplicates,
            "duplicate_keys_counted_over": (
                "every emitted row, BEFORE the FromCache filter: a fresh row "
                "plus a cached row for one view is a duplicate emission, and "
                "filtering first would hide exactly that case"
            ),
        }
        if not has_from_cache:
            # Stated, not assumed to be zero. A cached view's row in this file
            # cannot be identified as cached at all, which is an asymmetry
            # against views_core/views_vop and is the operator's to know.
            record["from_cache_note"] = (
                "this role has no FromCache column, so cached rows in it "
                "cannot be identified or counted; the dropped count is "
                "unknown, not zero"
            )
        if duplicates:
            record["status"] = STATUS_VIOLATED
            findings.append({
                "violation_id": "I2.DUPLICATE_VIEW_ROW",
                "invariant": "I2",
                "summary": (
                    "{0}: {1} (RunId, ViewId) key(s) were emitted on more "
                    "than one row".format(role, len(duplicates))
                ),
                # view_ids travels with the detail so L3 can select the
                # views a duplication names; without it this is the one
                # violation class with no reachable exemplar.
                "detail": {"role": role, "duplicate_keys": duplicates,
                           "view_ids": sorted(duplicate_view_ids)},
            })
        else:
            record["status"] = STATUS_HOLDS
        per_role[role] = record

    total_dropped = sum(
        r["rows_dropped_from_cache"] for r in per_role.values()
        if isinstance(r, dict) and r.get("rows_dropped_from_cache") is not None
    )
    result = {
        "id": "I2",
        "name": "one row per (RunId, ViewId, path); FromCache dropped and counted",
        "path_note": (
            "path is declared at bundle level (L0), so within this bundle the "
            "key reduces to (RunId, ViewId)"
        ),
        "declared_cache_mode": cache_mode,
        "total_rows_dropped_from_cache": total_dropped,
        "cache_expectation": (
            "NONE DECLARED. No count is expected in either direction: the "
            "cache is under review, and an assertion here would encode "
            "current behaviour as a rule."
        ),
        "per_role": per_role,
        "status": STATUS_VIOLATED if findings else STATUS_HOLDS,
    }
    return result, findings, retained_by_role


# ---------------------------------------------------------------------------
# I3 -- capped/adaptive => IsOnSheet False (ONE WAY)
# ---------------------------------------------------------------------------

def invariant_3_tier_one_way(loaded, retained_core):
    """Evaluated per CAP KIND, because "cap" is ambiguous in this codebase.

    ``CapTriggered``  -- views_core / views_vop -- is the cell-size /
    analysis-grid cap. ``cap_applied`` -- the color-ID sidecar -- is the
    export-pixel cap. They fire on different sets of views. The output names
    which one every time; an unqualified "cap" would be read as whichever the
    reader had in mind.
    """
    core = loaded.get("views_core")
    results = {}
    findings = []

    # --- export-pixel cap: not in this bundle's inputs, stated as such ---
    results["EXPORT_PIXEL_CAP"] = {
        "cap_field": "cap_applied",
        "source": "color-ID sidecar JSON",
        "status": STATUS_NOT_EVALUABLE,
        "reason": (
            "the sidecar is not one of this bundle's inputs (the bundle is "
            "the four CSVs); this cap is named here so that the CELL_SIZE_CAP "
            "result below is never read as covering it"
        ),
    }

    # --- cell-size / analysis-grid cap ---
    record = {
        "cap_field": "CapTriggered",
        "source": "views_core",
        "direction": "capped/adaptive => IsOnSheet False",
        "converse": (
            "NOT ASSERTED AND NOT INFERRED. A non-capped view is not thereby "
            "sheeted."
        ),
    }
    missing = [c for c in ("CapTriggered", "ResolutionMode", "IsOnSheet")
               if c not in core["fieldnames"]]
    if missing:
        record["status"] = STATUS_NOT_EVALUABLE
        record["reason"] = "views_core lacks column(s): {0}".format(
            ", ".join(missing))
        results["CELL_SIZE_CAP"] = record
        return {"id": "I3", "name": "tier one-way invariant",
                "per_cap": results,
                "status": STATUS_NOT_EVALUABLE}, findings

    sheet_values = []
    antecedent_rows = []
    violating = []
    indeterminate = []
    for entry in retained_core:
        row = entry["raw"]
        idx = entry["row_index"]
        on_sheet = parse_tribool(row.get("IsOnSheet"), "IsOnSheet",
                                 "views_core", idx)
        capped = parse_tribool(row.get("CapTriggered"), "CapTriggered",
                               "views_core", idx)
        mode_raw = row.get("ResolutionMode")
        mode = "" if mode_raw is None else str(mode_raw).strip().lower()
        sheet_values.append(on_sheet)
        is_antecedent = (capped is True) or (mode == "adaptive")
        if is_antecedent:
            antecedent_rows.append({
                "row_index": idx,
                "view_id": entry["view_id"],
                "cap_triggered": capped,
                "resolution_mode": mode_raw,
                "is_on_sheet": on_sheet,
            })
            if on_sheet is True:
                violating.append(antecedent_rows[-1])
            elif on_sheet is None:
                # parse_tribool preserves an empty cell as None precisely so
                # that "never written" stays distinguishable from "written
                # false". Treating it as satisfying the consequent here would
                # throw that distinction away at the call site and certify an
                # implication over a row whose consequent was never recorded.
                indeterminate.append(antecedent_rows[-1])

    distinct_sheet = sorted({str(v) for v in sheet_values})
    any_true = any(v is True for v in sheet_values)
    record["antecedent_rows"] = len(antecedent_rows)
    record["antecedent_rows_indeterminate"] = len(indeterminate)
    record["is_on_sheet_distinct_values"] = distinct_sheet
    record["is_on_sheet_true_count"] = sum(1 for v in sheet_values if v is True)
    record["is_on_sheet_false_count"] = sum(1 for v in sheet_values if v is False)
    record["is_on_sheet_unpopulated_count"] = sum(
        1 for v in sheet_values if v is None)

    if not any_true:
        # WHY this is NOT_EXERCISED rather than HOLDS.
        #
        # IsOnSheet is initialised False and only ever set True by a Viewport
        # scan (csv_export.py extract_view_metadata). It therefore reads False
        # both when a view is genuinely not on a sheet AND when the scan could
        # not run at all -- Viewport unimportable, doc None, the collector
        # raising. With no True anywhere in the run, the column cannot
        # distinguish those, so an implication whose consequent is "False"
        # would be satisfied by a field that was never populated.
        #
        # This state SELF-RETIRES: the first run containing a True proves the
        # scan runs, and the invariant is evaluated normally from then on.
        record["status"] = STATUS_NOT_EXERCISED
        record["reason"] = (
            "no row carries IsOnSheet True, so the column cannot distinguish "
            "'nothing is on a sheet' from 'the viewport scan never ran'. "
            "Reporting HOLDS here would report clean for precisely the reason "
            "the data is uninformative. Self-retires on the first run "
            "containing a True."
        )
    elif not antecedent_rows:
        record["status"] = STATUS_NOT_EXERCISED
        record["reason"] = (
            "no row is capped or adaptive, so the implication has no "
            "antecedent to test"
        )
    elif violating:
        # An observed violation is a fact and outranks an undecidable row.
        record["status"] = STATUS_VIOLATED
        record["violating_rows"] = violating
        if indeterminate:
            record["indeterminate_rows"] = indeterminate
        findings.append({
            "violation_id": "I3.CAPPED_VIEW_ON_SHEET",
            "invariant": "I3",
            "summary": (
                "{0} capped/adaptive view(s) report IsOnSheet True "
                "(cell-size cap)".format(len(violating))
            ),
            "detail": {"cap_kind": "CELL_SIZE_CAP", "rows": violating},
        })
    elif indeterminate:
        record["status"] = STATUS_NOT_EVALUABLE
        record["indeterminate_rows"] = indeterminate
        record["reason"] = (
            "{0} capped/adaptive row(s) carry an UNPOPULATED IsOnSheet. The "
            "implication requires the consequent to be explicitly False; an "
            "unwritten cell does not record that it is, so reporting HOLDS "
            "would certify the invariant over rows whose consequent was never "
            "captured. Populate IsOnSheet for those rows, or accept that this "
            "capture cannot decide it.".format(len(indeterminate))
        )
    else:
        record["status"] = STATUS_HOLDS

    results["CELL_SIZE_CAP"] = record
    overall = record["status"]
    return ({"id": "I3",
             "name": "capped/adaptive => IsOnSheet False (one-way)",
             "per_cap": results,
             "status": overall}, findings)


# ---------------------------------------------------------------------------
# I4 -- every requested value ships with effective + ratio
# ---------------------------------------------------------------------------

def invariant_4_requested_effective(loaded, retained_by_role):
    """Detected as a CLASS, by name, not from a hardcoded list of pairs.

    Requested-read-as-achieved has shipped at least three times here
    (export_dpi, CellSizeRequested vs CellSizeEffective, and suspected on
    IsOnSheet). A hardcoded pair list would bind this check to the incidents
    already known; deriving the partner name from the column name means a
    NEW ``<X>Requested<suffix>`` column landing without its
    ``<X>Effective<suffix>`` partner fires this on the first run that carries
    it, with no edit here.

    The assertion is that the PAIR SHIPS. Nothing asserts requested ==
    effective: that they differ is the measurement, not the defect.
    """
    per_role = {}
    findings = []
    ratio_series = {}
    for role in REQUIRED_ROLES + OPTIONAL_ROLES:
        role_data = loaded.get(role)
        if role_data is None:
            per_role[role] = {"status": STATUS_NOT_EVALUABLE,
                              "reason": "role absent from bundle"}
            continue
        fields = role_data["fieldnames"]
        requested_columns = [f for f in fields if "Requested" in f]
        pairs = []
        unpaired = []
        for column in requested_columns:
            partner = column.replace("Requested", "Effective")
            if partner in fields:
                pairs.append({"requested": column, "effective": partner})
            else:
                unpaired.append({"requested": column,
                                 "expected_effective": partner})
        record = {
            "requested_columns": requested_columns,
            "pairs": pairs,
            "unpaired_requested_columns": unpaired,
        }
        if not requested_columns:
            record["status"] = STATUS_NOT_EXERCISED
            record["reason"] = "this role emits no Requested column"
            per_role[role] = record
            continue
        if unpaired:
            record["status"] = STATUS_VIOLATED
            findings.append({
                "violation_id": "I4.REQUESTED_WITHOUT_EFFECTIVE",
                "invariant": "I4",
                "summary": (
                    "{0}: {1} Requested column(s) ship without an Effective "
                    "partner".format(role, len(unpaired))
                ),
                "detail": {"role": role, "unpaired": unpaired},
            })
        else:
            record["status"] = STATUS_HOLDS

        measured = []
        for pair in pairs:
            series = []
            for entry in retained_by_role.get(role, []):
                requested = parse_number(entry["raw"].get(pair["requested"]),
                                         pair["requested"], role,
                                         entry["row_index"])
                effective = parse_number(entry["raw"].get(pair["effective"]),
                                         pair["effective"], role,
                                         entry["row_index"])
                series.append({
                    "view_id": entry["view_id"],
                    "requested": _round6(requested),
                    "effective": _round6(effective),
                    "ratio_effective_over_requested": _ratio(
                        effective, requested,
                        "{0}.{1} effective/requested (view {2})".format(
                            role, pair["requested"], entry["view_id"])),
                })
            key = "{0}.{1}".format(role, pair["requested"])
            ratio_series[key] = series
            measured.append({
                "pair": pair,
                "ratio_summary": summarize(
                    [s["ratio_effective_over_requested"] for s in series],
                    "{0} effective/requested".format(key)),
                "rows_where_requested_equals_effective": sum(
                    1 for s in series
                    if s["requested"] is not None
                    and s["requested"] == s["effective"]),
                "rows_total": len(series),
            })
        record["measured"] = measured
        per_role[role] = record

    statuses = [r.get("status") for r in per_role.values()]
    if STATUS_VIOLATED in statuses:
        overall = STATUS_VIOLATED
    elif STATUS_HOLDS in statuses:
        overall = STATUS_HOLDS
    else:
        overall = STATUS_NOT_EXERCISED
    return ({"id": "I4",
             "name": "every requested value ships with effective + ratio",
             "assertion": "the PAIR ships; requested == effective is NOT asserted",
             "per_role": per_role,
             "status": overall}, findings, ratio_series)


# ---------------------------------------------------------------------------
# I6 -- row-count checksums
# ---------------------------------------------------------------------------

def invariant_6_checksums(loaded, keyed_by_role, retained_by_role):
    per_role = {}
    for role in REQUIRED_ROLES + OPTIONAL_ROLES:
        role_data = loaded.get(role)
        if role_data is None:
            per_role[role] = {"present": False,
                              "note": "optional role absent from bundle"}
            continue
        keyed = keyed_by_role.get(role) or []
        retained = retained_by_role.get(role) or []
        per_role[role] = {
            "present": True,
            "file": role_data["basename"],
            "sha256": role_data["sha256"],
            "bytes": role_data["bytes"],
            "column_count": len(role_data["fieldnames"]),
            "rows_read": len(keyed),
            "rows_retained": len(retained),
            "rows_dropped": len(keyed) - len(retained),
            "distinct_view_ids_retained": len(
                {e["view_id"] for e in retained}),
            "distinct_run_ids": sorted({e["run_id"] for e in keyed}),
        }
    return {"id": "I6", "name": "row-count checksums",
            "status": STATUS_REPORT_ONLY, "per_role": per_role}


# ---------------------------------------------------------------------------
# I7 -- independently emitted cell totals: REPORT ONLY, NO ASSERTION
# ---------------------------------------------------------------------------

def invariant_7_cell_totals(loaded, retained_by_role):
    """Emits both totals and their ratio. Asserts NOTHING about agreement.

    The two totals are not expected to agree -- they count different
    populations by construction -- so an equality assertion would fire on
    every run and a tolerance would launder a definitional gap into something
    that reads like a measurement. The real fix is a declared definition of
    what each total counts, which is out of scope for this unit.

    This follows invariant 4's requested/effective/ratio shape deliberately:
    ship both numbers and the ratio, assert neither.
    """
    perf = loaded.get("views_perf")
    vop = loaded.get("views_vop")
    record = {
        "id": "I7",
        "name": "independently emitted cell totals",
        "status": STATUS_REPORT_ONLY,
        "assertion": (
            "NONE. No equality assertion and no tolerance -- by decision, not "
            "by omission."
        ),
        "total_a": "FilledCells (views_perf)",
        "total_b": "ModelOnly + Overlap + AnnoOnly (views_vop)",
    }
    missing = []
    if "FilledCells" not in perf["fieldnames"]:
        missing.append("views_perf.FilledCells")
    for column in ("ModelOnly", "Overlap", "AnnoOnly"):
        if column not in vop["fieldnames"]:
            missing.append("views_vop." + column)
    if missing:
        record["status"] = STATUS_NOT_EVALUABLE
        record["reason"] = "missing column(s): {0}".format(", ".join(missing))
        return record, {}

    perf_by_key = {(e["run_id"], e["view_id"]): e
                   for e in retained_by_role.get("views_perf", [])}
    vop_by_key = {(e["run_id"], e["view_id"]): e
                  for e in retained_by_role.get("views_vop", [])}

    per_view = {}
    unjoinable_perf_only = []
    unjoinable_vop_only = []
    for key, entry in perf_by_key.items():
        if key not in vop_by_key:
            unjoinable_perf_only.append(entry["view_id"])
    for key, entry in vop_by_key.items():
        if key not in perf_by_key:
            unjoinable_vop_only.append(entry["view_id"])

    for key in sorted(set(perf_by_key) & set(vop_by_key), key=lambda k: k[1]):
        perf_entry = perf_by_key[key]
        vop_entry = vop_by_key[key]
        total_a = parse_number(perf_entry["raw"].get("FilledCells"),
                               "FilledCells", "views_perf",
                               perf_entry["row_index"])
        parts = {}
        for column in ("ModelOnly", "Overlap", "AnnoOnly"):
            parts[column] = parse_number(vop_entry["raw"].get(column), column,
                                         "views_vop", vop_entry["row_index"])
        if any(v is None for v in parts.values()):
            total_b = None
        else:
            total_b = sum(parts.values())
        # Keyed by the FULL join key, not by view_id alone: a bundle carrying
        # two RunIds (itself an I1 violation, but the bundle is still emitted)
        # would otherwise have both rows for a view silently share whichever
        # totals were written last.
        per_view[key] = {
            "view_id": key[1],
            "total_a_filled_cells": _round6(total_a),
            "total_b_components": {k: _round6(v) for k, v in parts.items()},
            "total_b_sum": _round6(total_b),
            "ratio_b_over_a": _ratio(
                total_b, total_a,
                "cell totals b/a (view {0})".format(key[1])),
            "difference_b_minus_a": (
                None if (total_a is None or total_b is None)
                else _round6(total_b - total_a)),
        }

    record["views_joined"] = len(per_view)
    record["views_in_perf_only"] = sorted(unjoinable_perf_only)
    record["views_in_vop_only"] = sorted(unjoinable_vop_only)
    record["ratio_summary"] = summarize(
        [v["ratio_b_over_a"] for v in per_view.values()],
        "total_b/total_a")
    record["views_where_totals_are_equal"] = sum(
        1 for v in per_view.values()
        if v["total_a_filled_cells"] is not None
        and v["total_a_filled_cells"] == v["total_b_sum"])
    return record, per_view


# ---------------------------------------------------------------------------
# I8 -- frame-hash uniqueness across distinct (ViewId, grid dims)
# ---------------------------------------------------------------------------

def invariant_8_frame_hash(loaded, retained_by_role):
    core = loaded.get("views_core")
    perf = loaded.get("views_perf")
    record = {
        "id": "I8",
        "name": "frame-hash uniqueness across distinct (ViewId, grid dims)",
    }
    missing = []
    if "ViewFrameHash" not in core["fieldnames"]:
        missing.append("views_core.ViewFrameHash")
    for column in ("Width", "Height"):
        if column not in perf["fieldnames"]:
            missing.append("views_perf." + column)
    if missing:
        record["status"] = STATUS_NOT_EVALUABLE
        record["reason"] = (
            "missing column(s): {0}. Grid dimensions live in views_perf while "
            "the hash lives in views_core, so this invariant needs both."
            .format(", ".join(missing))
        )
        return record, []

    perf_by_key = {(e["run_id"], e["view_id"]): e
                   for e in retained_by_role.get("views_perf", [])}
    by_hash = defaultdict(set)
    unjoinable = []
    evaluated = 0
    for entry in retained_by_role.get("views_core", []):
        raw_hash = entry["raw"].get("ViewFrameHash")
        frame_hash = "" if raw_hash is None else str(raw_hash).strip()
        key = (entry["run_id"], entry["view_id"])
        perf_entry = perf_by_key.get(key)
        if frame_hash == "" or perf_entry is None:
            unjoinable.append({
                "view_id": entry["view_id"],
                "reason": ("empty ViewFrameHash" if frame_hash == ""
                           else "no views_perf row for this key"),
            })
            continue
        width = parse_int(perf_entry["raw"].get("Width"), "Width",
                          "views_perf", perf_entry["row_index"])
        height = parse_int(perf_entry["raw"].get("Height"), "Height",
                           "views_perf", perf_entry["row_index"])
        if width is None or height is None:
            unjoinable.append({"view_id": entry["view_id"],
                               "reason": "Width or Height unpopulated"})
            continue
        evaluated += 1
        by_hash[frame_hash].add((entry["view_id"], width, height))

    collisions = {}
    for frame_hash, keys in by_hash.items():
        if len(keys) > 1:
            collisions[frame_hash] = sorted(
                [{"view_id": k[0], "width": k[1], "height": k[2],
                  "cells": k[1] * k[2]} for k in keys],
                key=lambda d: d["view_id"])

    record["rows_evaluated"] = evaluated
    record["distinct_hashes"] = len(by_hash)
    record["rows_unjoinable"] = unjoinable
    record["collisions"] = collisions
    findings = []
    if evaluated == 0:
        record["status"] = STATUS_NOT_EXERCISED
        record["reason"] = (
            "no row carries both a ViewFrameHash and joinable grid "
            "dimensions, so uniqueness has nothing to range over"
        )
    elif collisions:
        # An observed collision is a fact and outranks an incomplete
        # population, exactly as in I3.
        record["status"] = STATUS_VIOLATED
        findings.append({
            "violation_id": "I8.VIEW_FRAME_HASH_COLLISION",
            "invariant": "I8",
            "summary": (
                "{0} ViewFrameHash value(s) cover more than one distinct "
                "(ViewId, grid dims) key".format(len(collisions))
            ),
            "detail": {"collisions": collisions},
        })
    elif unjoinable:
        # Uniqueness is a claim ABOUT A POPULATION. Rows that could not be
        # keyed are not in it, so reporting HOLDS would certify the claim over
        # a set that excludes precisely the rows nothing could check -- and a
        # collision among them would be invisible.
        record["status"] = STATUS_NOT_EVALUABLE
        record["reason"] = (
            "{0} of {1} row(s) could not be joined to a (ViewFrameHash, grid "
            "dims) key, so uniqueness would be certified over an incomplete "
            "population. Populate the missing values, or accept that this "
            "capture cannot decide it.".format(
                len(unjoinable), len(unjoinable) + evaluated)
        )
    else:
        record["status"] = STATUS_HOLDS
    return record, findings


# ---------------------------------------------------------------------------
# L1 -- per-view narrow row
# ---------------------------------------------------------------------------

def build_l1(retained_by_role, cell_totals_by_view):
    core_by_key = {(e["run_id"], e["view_id"]): e
                   for e in retained_by_role.get("views_core", [])}
    vop_by_key = {(e["run_id"], e["view_id"]): e
                  for e in retained_by_role.get("views_vop", [])}
    perf_by_key = {(e["run_id"], e["view_id"]): e
                   for e in retained_by_role.get("views_perf", [])}

    rows = []
    for key in sorted(set(core_by_key) | set(vop_by_key) | set(perf_by_key),
                      key=lambda k: (k[0], k[1])):
        core = core_by_key.get(key)
        vop = vop_by_key.get(key)
        perf = perf_by_key.get(key)
        core_raw = core["raw"] if core else {}
        vop_raw = vop["raw"] if vop else {}
        perf_raw = perf["raw"] if perf else {}
        view_type = str(core_raw.get("ViewType")
                        or vop_raw.get("ViewType") or "").strip()
        requested = parse_number(core_raw.get("CellSizeRequested_ft"),
                                 "CellSizeRequested_ft", "views_core",
                                 core["row_index"] if core else 0)
        effective = parse_number(core_raw.get("CellSizeEffective_ft"),
                                 "CellSizeEffective_ft", "views_core",
                                 core["row_index"] if core else 0)
        width = parse_int(perf_raw.get("Width"), "Width", "views_perf",
                          perf["row_index"] if perf else 0)
        height = parse_int(perf_raw.get("Height"), "Height", "views_perf",
                           perf["row_index"] if perf else 0)
        anno = {}
        for category in ANNO_CATEGORIES:
            column = "AnnoCells_" + category
            anno[category] = parse_number(
                vop_raw.get(column), column, "views_vop",
                vop["row_index"] if vop else 0)
        totals = cell_totals_by_view.get(key, {})
        rows.append({
            "run_id": key[0],
            "view_id": key[1],
            "view_name": core_raw.get("ViewName") or vop_raw.get("ViewName"),
            "view_type": view_type or VIEWTYPE_UNSET,
            "present_in": {
                "views_core": core is not None,
                "views_vop": vop is not None,
                "views_perf": perf is not None,
            },
            "is_on_sheet": parse_tribool(
                core_raw.get("IsOnSheet"), "IsOnSheet", "views_core",
                core["row_index"] if core else 0),
            "resolution_mode": core_raw.get("ResolutionMode"),
            "cap_triggered_cell_size": parse_tribool(
                core_raw.get("CapTriggered"), "CapTriggered", "views_core",
                core["row_index"] if core else 0),
            "cell_size_requested_ft": _round6(requested),
            "cell_size_effective_ft": _round6(effective),
            "cell_size_ratio_effective_over_requested": _ratio(
                effective, requested,
                "cell size effective/requested (view {0})".format(key[1])),
            "view_frame_hash": core_raw.get("ViewFrameHash"),
            "grid_width": width,
            "grid_height": height,
            "grid_cells": (None if (width is None or height is None)
                           else width * height),
            "total_cells_declared": parse_number(
                vop_raw.get("TotalCells"), "TotalCells", "views_vop",
                vop["row_index"] if vop else 0),
            "cell_total_a_filled_cells": totals.get("total_a_filled_cells"),
            "cell_total_b_sum": totals.get("total_b_sum"),
            "cell_total_ratio_b_over_a": totals.get("ratio_b_over_a"),
            "anno_cells": {k: _round6(v) for k, v in anno.items()},
        })
    return rows


# ---------------------------------------------------------------------------
# L2 -- per-category, keyed (ViewType x Category)
# ---------------------------------------------------------------------------

def build_l2(l1_rows):
    """Cells keyed (ViewType x Category) over the AnnoCells_* buckets.

    n is the number of views of that ViewType carrying a NON-ZERO value for
    the category. A zero is "this category does not appear in this view", not
    an observation of its magnitude, and counting zeros would make n identical
    across every category in a ViewType and the rollup meaningless.
    ``n_views_in_view_type`` is carried alongside so that distinction is
    visible rather than buried in this docstring.

    Cells below the floor roll into OTHER_ROLLUP with their n RETAINED. That
    key is deliberately NOT "OTHER": AnnoCells_OTHER is a real annotation
    bucket the pipeline emits, and two different things must not share a name.
    """
    by_view_type = defaultdict(list)
    for row in l1_rows:
        by_view_type[row["view_type"]].append(row)

    l2 = {}
    for view_type, rows in sorted(by_view_type.items()):
        cells = {}
        rolled = []
        rollup_values = []
        rollup_n = 0
        for category in ANNO_CATEGORIES:
            values = [r["anno_cells"].get(category) for r in rows]
            contributing = [v for v in values if v is not None and v > 0]
            cell = {
                "category": category,
                "n": len(contributing),
                "n_views_in_view_type": len(rows),
                "n_zero_or_absent": len(rows) - len(contributing),
                "sum": _round6(sum(contributing)) if contributing else 0.0,
            }
            if len(contributing) < N_MIN_L2_ROLLUP:
                cell["rolled_into"] = L2_ROLLUP_KEY
                cell["rollup_reason"] = (
                    "n={0} < n_min={1}".format(
                        len(contributing), N_MIN_L2_ROLLUP))
                rolled.append(cell)
                rollup_values.extend(contributing)
                rollup_n += len(contributing)
            else:
                cell["summary"] = summarize(
                    contributing,
                    "{0}/{1} cells".format(view_type, category))
                cells[category] = cell
        if rolled:
            cells[L2_ROLLUP_KEY] = {
                "category": L2_ROLLUP_KEY,
                "distinct_from": (
                    "the AnnoCells_OTHER annotation bucket, which is a real "
                    "category and rolls in here like any other when its own n "
                    "is below the floor"
                ),
                "n": rollup_n,
                "categories_rolled_in": [c["category"] for c in rolled],
                "per_category_n": {c["category"]: c["n"] for c in rolled},
                "sum": _round6(sum(rollup_values)) if rollup_values else 0.0,
                "summary": summarize(
                    rollup_values, "{0}/{1}".format(view_type, L2_ROLLUP_KEY)),
            }
        l2[view_type] = {
            "view_type": view_type,
            "n_views": len(rows),
            "n_min_rollup": N_MIN_L2_ROLLUP,
            "cells": cells,
        }
    return l2


# ---------------------------------------------------------------------------
# L3 -- raw exemplar rows, selected by rule
# ---------------------------------------------------------------------------

L3_TARGET_MIN = 20
L3_TARGET_MAX = 40


def build_l3(l1_rows, findings):
    """Exemplars by RULE, never by hand-picking.

    The rule: each view's max and min annotation category, every view named by
    a violation, and a median draw by grid_cells. The selection_reason travels
    with each exemplar so a reader can tell why it is here without re-deriving
    the rule.

    If the rule yields more than L3_TARGET_MAX, the excess is truncated and
    the truncation is RECORDED. If it yields fewer than L3_TARGET_MIN, that is
    what the data supports and nothing is padded to reach the target.
    """
    selected = {}

    def add(row, reason):
        marker = (row["view_id"], reason)
        if marker not in selected:
            selected[marker] = {
                "view_id": row["view_id"],
                "view_name": row["view_name"],
                "view_type": row["view_type"],
                "selection_reason": reason,
                "row": row,
            }

    violation_view_ids = set()
    for finding in findings:
        detail = finding.get("detail") or {}
        for value in _walk_view_ids(detail):
            violation_view_ids.add(value)

    for row in l1_rows:
        present = {k: v for k, v in row["anno_cells"].items()
                   if v is not None and v > 0}
        if present:
            max_category = max(present, key=lambda k: present[k])
            min_category = min(present, key=lambda k: present[k])
            add(row, "view max annotation category: {0}={1}".format(
                max_category, present[max_category]))
            if min_category != max_category:
                add(row, "view min annotation category: {0}={1}".format(
                    min_category, present[min_category]))
        if row["view_id"] in violation_view_ids:
            add(row, "named by a violation")

    sized = [r for r in l1_rows if r["grid_cells"] is not None]
    if sized:
        ordered = sorted(sized, key=lambda r: r["grid_cells"])
        median_row = ordered[len(ordered) // 2]
        add(median_row, "median draw by grid_cells")

    # Violation exemplars are RESERVED before truncation. Sorting everything
    # by view_id and slicing would drop them by position: a 30-view bundle
    # whose collision is between its first and last view kept the first
    # exemplar and silently discarded the second -- contradicting the
    # selection rule printed alongside it, which promises every violation.
    ordered = sorted(selected.values(),
                     key=lambda e: (e["view_id"], e["selection_reason"]))
    reserved = [e for e in ordered
                if e["selection_reason"] == "named by a violation"]
    optional = [e for e in ordered
                if e["selection_reason"] != "named by a violation"]
    room = max(L3_TARGET_MAX - len(reserved), 0)
    exemplars = sorted(reserved + optional[:room],
                       key=lambda e: (e["view_id"], e["selection_reason"]))
    truncated = len(optional) - min(len(optional), room)
    violations_over_cap = max(len(reserved) - L3_TARGET_MAX, 0)
    return {
        "target_range": [L3_TARGET_MIN, L3_TARGET_MAX],
        "selection_rule": (
            "each view's max and min annotation category; every view named by "
            "a violation; one median draw by grid_cells"
        ),
        "selected": len(exemplars),
        "truncated": truncated,
        "truncated_are_all_lower_priority": True,
        "violation_exemplars": len(reserved),
        "violation_exemplars_over_cap": violations_over_cap,
        "over_cap_note": (
            "violation exemplars are reserved before truncation, so the cap "
            "can be exceeded rather than drop one; a non-zero "
            "violation_exemplars_over_cap records by how much"
        ),
        "below_target_min": len(exemplars) < L3_TARGET_MIN,
        "below_target_note": (
            "fewer exemplars than the target minimum means the rule yielded "
            "that many over this bundle; nothing is padded to reach 20"
        ),
        "exemplars": exemplars,
    }


def _walk_view_ids(node):
    """Yield every ``view_id`` value nested anywhere inside a finding detail."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "view_id" and isinstance(value, int):
                yield value
            elif key == "view_ids" and isinstance(value, list):
                for item in value:
                    if isinstance(item, int):
                        yield item
            else:
                for found in _walk_view_ids(value):
                    yield found
    elif isinstance(node, list):
        for item in node:
            for found in _walk_view_ids(item):
                yield found


# ---------------------------------------------------------------------------
# L4 -- violation register, against the known-open baseline
# ---------------------------------------------------------------------------

def build_l4(findings, run_id):
    """A register, never a pass/fail verdict.

    Every finding is classified against the known-open baseline so the
    post-deletion read is NEW violations only. Baseline entries this tool
    version cannot detect are carried forward as KNOWN_OPEN_UNDETECTED: their
    absence from ``findings`` is not evidence that they were fixed, and the
    register says so in-band rather than relying on a reader remembering it.
    """
    observed = []
    new = []
    for finding in findings:
        entry = dict(finding)
        baseline = KNOWN_OPEN_BASELINE.get(finding["violation_id"])
        if baseline is None:
            entry["baseline_status"] = "NEW"
            entry["first_observed_run"] = run_id
            new.append(entry)
        else:
            entry["baseline_status"] = "KNOWN_OPEN"
            entry["first_observed_run"] = baseline["first_observed_run"]
        observed.append(entry)

    observed_ids = {f["violation_id"] for f in findings}
    carried = []
    for violation_id, baseline in sorted(KNOWN_OPEN_BASELINE.items()):
        if violation_id in observed_ids:
            continue
        record = {
            "violation_id": violation_id,
            "first_observed_run": baseline["first_observed_run"],
            "summary": baseline["summary"],
            "detector": baseline["detector"],
            "detector_mode": baseline["detector_mode"],
        }
        if baseline["detector"] is None:
            record["baseline_status"] = "KNOWN_OPEN_UNDETECTED"
            record["absence_means"] = (
                "NOTHING. This tool version has no detector for it, so its "
                "absence from this run's findings is not evidence of a fix."
            )
            record["why_no_detector"] = baseline.get("why_no_detector")
        elif baseline["detector_mode"] == "REPORT_ONLY_NO_ASSERTION":
            record["baseline_status"] = "KNOWN_OPEN_REPORT_ONLY"
            record["absence_means"] = (
                "NOTHING. The detector emits measurements but asserts "
                "nothing, so it can never produce a finding to match."
            )
            record["why_not_asserted"] = baseline.get("why_not_asserted")
        else:
            record["baseline_status"] = "NOT_OBSERVED_THIS_RUN"
            record["absence_means"] = (
                "the active detector ran over this bundle and did not fire"
            )
        carried.append(record)

    return {
        "reported_as": "register",
        "not_reported_as": "pass/fail",
        "baseline_seed_run": "20260917T112500",
        "baseline_seed_warning": (
            "That run is DIRTY. The baseline seeds the EXISTENCE of these "
            "violations only. The recorded figures are an example of the data "
            "type and are not evidence, not ground truth, and not a prior."
        ),
        "observed": observed,
        "new_violations": new,
        "new_violation_count": len(new),
        "carried_from_baseline": carried,
    }


# ---------------------------------------------------------------------------
# L0 -- manifest. What makes the bundle self-describing as lossy.
# ---------------------------------------------------------------------------

def _single_value(loaded, column, roles):
    """Distinct non-empty values of a column across roles."""
    values = set()
    for role in roles:
        role_data = loaded.get(role)
        if role_data is None or column not in role_data["fieldnames"]:
            continue
        for row in role_data["rows"]:
            raw = row.get(column)
            text = "" if raw is None else str(raw).strip()
            if text:
                values.add(text)
    return sorted(values)


def build_l0(loaded, resolved, run_ids, checksums, i2, declared, diagnostics):
    config_hashes = _single_value(loaded, "ConfigHash",
                                  ("views_core", "views_vop"))
    exporter_versions = _single_value(loaded, "ExporterVersion",
                                      ("views_core", "views_vop"))
    if len(config_hashes) > 1:
        # Reuses the EXISTING campaign-fingerprint drift semantics by name.
        # A bundle spanning two configurations is not one run, and verifying
        # it as one would attribute a finding to a configuration that did not
        # produce it. No second drift scheme is invented here.
        raise Refusal(
            "CONFIGURATION_DRIFT: the bundle carries {0} distinct ConfigHash "
            "values ({1}). A bundle spanning two configurations is not one "
            "run. Separate them, or supersede the prior jobs as the campaign "
            "planner already does on a fingerprint change.".format(
                len(config_hashes), ", ".join(config_hashes))
        )

    dropped = {}
    for role, record in i2["per_role"].items():
        if not isinstance(record, dict) or "rows_read" not in record:
            continue
        dropped[role] = {
            "rows_read": record["rows_read"],
            "rows_dropped_from_cache": record["rows_dropped_from_cache"],
            "rows_retained": record["rows_retained"],
            "why_dropped": (
                "FromCache is True (invariant 2)"
                if record["from_cache_column_present"]
                else "no FromCache column in this role; nothing could be "
                     "identified as cached, so the dropped count is UNKNOWN, "
                     "not zero"
            ),
        }

    return {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "run_id": run_ids[0] if len(run_ids) == 1 else None,
        "run_ids_observed": run_ids,
        "path": declared["path"],
        "path_provenance": (
            "declared by the operator via --path and recorded here. No CSV "
            "column discriminates the path: RowSource is a hardcoded constant "
            "written unconditionally by both paths."
        ),
        "document": declared["document"],
        "document_provenance": (
            "declared by the operator via --document"
            if declared["document"] is not None
            else "NOT DECLARED. No bundle CSV carries the document identity, "
                 "so it is absent rather than inferred."
        ),
        "config_hash": config_hashes[0] if config_hashes else None,
        "config_hash_note": (
            "a change in this value is CONFIGURATION_DRIFT under the existing "
            "campaign-fingerprint semantics and supersedes prior jobs"
        ),
        "exporter_versions": exporter_versions,
        "declared_cache_mode": declared["cache_mode"],
        "source_artifacts": {
            "bundle_dir": os.path.abspath(declared["bundle_dir"]),
            "files": checksums,
            "roles_absent": [role for role, path in resolved.items()
                             if path is None],
            "diagnostics_json": diagnostics,
        },
        "rows": dropped,
        "lossiness": (
            "THIS BUNDLE IS LOSSY. It is a reviewable summary, not the run. "
            "Absence of a quantity here does NOT mean absence from the data. "
            "Every input file is checksummed above; read them, not this, when "
            "the question is what the run contained."
        ),
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def verify(bundle_dir, path, cache_mode, document=None,
           diagnostics_json=None):
    if path not in PATHS:
        raise Refusal(
            "--path must be one of {0}; got {1!r}".format(
                "/".join(PATHS), path))
    if cache_mode not in CACHE_MODES:
        raise Refusal(
            "--cache-mode must be one of {0}; got {1!r}".format(
                "/".join(CACHE_MODES), cache_mode))

    resolved = resolve_roles(bundle_dir)
    loaded = load_bundle(resolved)
    keyed_by_role = {role: key_rows(loaded.get(role))
                     for role in REQUIRED_ROLES + OPTIONAL_ROLES}

    findings = []
    i1, i1_findings = invariant_1_single_run_id(keyed_by_role)
    findings.extend(i1_findings)

    i2, i2_findings, retained_by_role = invariant_2_dedupe(
        loaded, keyed_by_role, cache_mode)
    findings.extend(i2_findings)

    i3, i3_findings = invariant_3_tier_one_way(
        loaded, retained_by_role.get("views_core", []))
    findings.extend(i3_findings)

    i4, i4_findings, _ratio_series = invariant_4_requested_effective(
        loaded, retained_by_role)
    findings.extend(i4_findings)

    i5 = {
        "id": "I5",
        "name": "no summary statistic without n",
        "status": STATUS_REPORT_ONLY,
        "n_min_order_statistic": N_MIN_ORDER_STATISTIC,
        "scope": (
            "order statistics only (median, p10, p90). Counts, sums, ratios, "
            "min and max are reported at ANY n, always with n alongside."
        ),
        "suppression_is_visible": (
            "a withheld statistic is emitted as a 'suppressed' record "
            "carrying n and the reason -- never omitted, because an absent "
            "key and a withheld key read identically to a consumer"
        ),
        "is_a_tolerance": False,
        "rationale": (
            "lifted from MIN_N_FOR_SUMMARY in "
            "tools/notes/g2_bbox_excursion_report.py: at n >= 8 at least "
            "three observations sit strictly each side of the median and both "
            "quartiles land at interior ranks. That argument covers order "
            "statistics and nothing else."
        ),
    }

    i6 = invariant_6_checksums(loaded, keyed_by_role, retained_by_role)
    i7, cell_totals_by_view = invariant_7_cell_totals(loaded, retained_by_role)
    i8, i8_findings = invariant_8_frame_hash(loaded, retained_by_role)
    findings.extend(i8_findings)

    l1 = build_l1(retained_by_role, cell_totals_by_view)
    l2 = build_l2(l1)
    l3 = build_l3(l1, findings)

    run_ids = i1["distinct_run_ids"]
    run_id = run_ids[0] if len(run_ids) == 1 else None
    l4 = build_l4(findings, run_id)

    checksums = {}
    for role in REQUIRED_ROLES + OPTIONAL_ROLES:
        record = i6["per_role"].get(role) or {}
        if record.get("present"):
            checksums[role] = {
                "file": record["file"],
                "sha256": record["sha256"],
                "bytes": record["bytes"],
            }

    diagnostics = None
    if diagnostics_json is not None:
        if not os.path.isfile(diagnostics_json):
            raise Refusal(
                "--diagnostics-json {0!r} does not exist. It is recorded for "
                "provenance, so a path that names nothing would put a false "
                "pointer in L0.".format(diagnostics_json)
            )
        sha256, size = _sha256_and_size(diagnostics_json)
        diagnostics = {
            "file": os.path.basename(diagnostics_json),
            "sha256": sha256,
            "bytes": size,
            "parsed": False,
            "note": "recorded for provenance; no invariant here reads it",
        }

    l0 = build_l0(loaded, resolved, run_ids, checksums, i2,
                  {"path": path, "cache_mode": cache_mode,
                   "document": document, "bundle_dir": bundle_dir},
                  diagnostics)

    return {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "L0_manifest": l0,
        "invariants": {
            "I1": i1, "I2": i2, "I3": i3, "I4": i4,
            "I5": i5, "I6": i6, "I7": i7, "I8": i8,
        },
        "L1_per_view": l1,
        "L2_per_category": l2,
        "L3_exemplars": l3,
        "L4_violation_register": l4,
    }


def load_verification_bundle(path):
    """Read a bundle this tool wrote, refusing an unsupported schema version."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    version = data.get("bundle_schema_version")
    if version is None:
        raise Refusal(
            "{0} carries no bundle_schema_version. An unversioned bundle "
            "cannot be read safely, because nothing says which contract its "
            "keys follow.".format(path)
        )
    if str(version) not in SUPPORTED_BUNDLE_SCHEMA_VERSIONS:
        raise Refusal(
            "UNSUPPORTED_BUNDLE_SCHEMA_VERSION: {0} declares {1!r}; this tool "
            "supports {2}. Reading it anyway would interpret its keys under a "
            "contract it was not written to.".format(
                path, version, sorted(SUPPORTED_BUNDLE_SCHEMA_VERSIONS))
        )
    return data


def summary_lines(bundle):
    lines = []
    l0 = bundle["L0_manifest"]
    lines.append("run_id        {0}".format(l0["run_id"]))
    lines.append("path          {0}  (declared)".format(l0["path"]))
    lines.append("config_hash   {0}".format(l0["config_hash"]))
    lines.append("cache_mode    {0}  (declared)".format(
        l0["declared_cache_mode"]))
    lines.append("")
    for key in ("I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8"):
        invariant = bundle["invariants"][key]
        lines.append("{0}  {1:<16} {2}".format(
            key, invariant["status"], invariant["name"]))
    lines.append("")
    register = bundle["L4_violation_register"]
    lines.append("register: {0} observed, {1} NEW, {2} carried from baseline"
                 .format(len(register["observed"]),
                         register["new_violation_count"],
                         len(register["carried_from_baseline"])))
    for entry in register["new_violations"]:
        lines.append("  NEW  {0}: {1}".format(
            entry["violation_id"], entry["summary"]))
    for entry in register["carried_from_baseline"]:
        if entry["baseline_status"] in (
                "KNOWN_OPEN_UNDETECTED", "KNOWN_OPEN_REPORT_ONLY"):
            lines.append("  {0}  {1} -- absence proves nothing".format(
                entry["baseline_status"], entry["violation_id"]))
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Emit the path-independent verification invariant bundle for a "
            "VOP run. Reports a REGISTER, not a pass/fail verdict."))
    parser.add_argument("bundle_dir",
                        help="directory holding the run's views_*.csv files")
    parser.add_argument("--path", required=True, choices=list(PATHS),
                        help=("which extraction path produced this bundle. "
                              "No CSV column discriminates it, so it is "
                              "declared here and recorded in L0."))
    parser.add_argument("--cache-mode", required=True, choices=list(CACHE_MODES),
                        help=("the run's declared cache mode. Recorded "
                              "alongside the dropped-row count; NO count is "
                              "expected in either direction."))
    parser.add_argument("--document", default=None,
                        help="optional document identity, recorded in L0")
    parser.add_argument("--diagnostics-json", default=None,
                        help="optional diagnostics JSON, checksummed into L0")
    parser.add_argument("--out", default=None,
                        help="write the bundle here (default: stdout)")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress the human-readable summary on stderr")
    parser.add_argument(
        "--fail-on-new", action="store_true",
        help=("exit 1 when a violation fires that the known-open baseline "
              "does not carry. OPT-IN: the default exit code reports whether "
              "the tool could DECIDE, not whether the data was good, so that "
              "a register is never silently read as a gate."))
    args = parser.parse_args(argv)

    try:
        bundle = verify(args.bundle_dir, args.path, args.cache_mode,
                        document=args.document,
                        diagnostics_json=args.diagnostics_json)
    except Refusal as refusal:
        sys.stderr.write("REFUSED: {0}\n".format(refusal))
        return 2

    text = json.dumps(bundle, indent=2, sort_keys=True)
    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        sys.stdout.write(text + "\n")

    if not args.quiet:
        sys.stderr.write("\n".join(summary_lines(bundle)) + "\n")

    if args.fail_on_new and bundle["L4_violation_register"]["new_violation_count"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
