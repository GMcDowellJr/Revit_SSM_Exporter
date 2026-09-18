# tests/conftest.py

import ast
import os
from pathlib import Path

import pytest

from tests.quarantine_registry import QUARANTINED

_ROOT = Path(__file__).resolve().parents[1]

QUARANTINE_MARKER = (
    "quarantine: a known, recorded failure. Requires a matching entry in "
    "tests/quarantine_registry.py; runs as a STRICT xfail constrained to the "
    "recorded exception type, so neither fixing the underlying defect nor "
    "failing a NEW way stays green."
)


def pytest_ignore_collect(collection_path: Path, config):
    """
    Prevent collection of Dynamo/Revit integration tests unless explicitly enabled.

    Enable by setting:
        VOP_RUN_DYNAMO_TESTS=1
    """
    run_dynamo = os.environ.get("VOP_RUN_DYNAMO_TESTS", "").strip() == "1"
    if run_dynamo:
        return False

    p = str(collection_path).replace("\\", "/")
    return "/tests/dynamo/" in p


def pytest_configure(config):
    config.addinivalue_line("markers", QUARANTINE_MARKER)


def _registry_key(item):
    """"<path relative to the repo root>::<test name>" for a collected item.

    Parametrized ids are stripped, so one registry entry covers a whole
    parametrization rather than needing one line per case.
    """
    rel = Path(item.fspath).resolve().relative_to(_ROOT).as_posix()
    name = item.originalname or item.name
    return "{0}::{1}".format(rel, name)


def _is_quarantine_decorator(node):
    """True for ``@pytest.mark.quarantine`` and ``@pytest.mark.quarantine(...)``."""
    if isinstance(node, ast.Call):
        node = node.func
    return isinstance(node, ast.Attribute) and node.attr == "quarantine"


def _declared_in_source(rel_path, test_name):
    """``(file_exists, test_exists, is_marked)`` read from the FILE, not from
    what this run happened to collect.

    The reverse check has to distinguish "this entry is stale" from "this run
    selected a different test", and collection cannot tell them apart:
    ``pytest <file>::<other_test>`` collects the file with the quarantined
    sibling absent, which is indistinguishable at the hook from a rename. So
    the file is read instead. This is immune to every selection mechanism --
    node ids, ``-k``, ``-m``, ``--lf`` -- rather than enumerating the ones
    thought of today.
    """
    path = _ROOT / rel_path
    if not path.is_file():
        return (False, False, False)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        # Unreadable or mid-edit: say nothing rather than fail a whole run on
        # the registry's behalf. A broken test file fails on its own terms.
        return (True, True, True)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name:
            return (True, True, any(_is_quarantine_decorator(d)
                                    for d in node.decorator_list))
    return (True, False, False)


def pytest_collection_modifyitems(session, config, items):
    """Bind the quarantine marker to the registry, in BOTH directions.

    A marker without an entry, or an entry whose test no longer carries the
    marker, is a hard error rather than a skip. Either one on its own is how a
    quarantine becomes an untracked permanent exception -- which is the thing
    the registry exists to prevent, so it cannot itself fail quietly.
    """
    marked = {}
    for item in items:
        if item.get_closest_marker("quarantine") is None:
            continue
        marked.setdefault(_registry_key(item), []).append(item)

    unregistered = sorted(set(marked) - set(QUARANTINED))
    if unregistered:
        raise pytest.UsageError(
            "test(s) marked @pytest.mark.quarantine with no entry in "
            "tests/quarantine_registry.py:\n  " + "\n  ".join(unregistered)
            + "\nAdd an entry stating the reason, the open question, the "
              "expected exception type and why it is not a flake, or remove "
              "the marker.")

    # Reverse direction, decided by reading the source rather than by guessing
    # whether this run collected a file "wholly". See _declared_in_source().
    stale = []
    for key in sorted(QUARANTINED):
        rel_path, test_name = key.split("::", 1)
        file_exists, test_exists, is_marked = _declared_in_source(rel_path, test_name)
        if not file_exists:
            stale.append("{0}  (file is gone)".format(key))
        elif not test_exists:
            stale.append("{0}  (no such test in that file)".format(key))
        elif not is_marked:
            stale.append("{0}  (test exists but carries no quarantine marker)".format(key))
    if stale:
        raise pytest.UsageError(
            "tests/quarantine_registry.py has stale entries:\n  "
            + "\n  ".join(stale)
            + "\nRemove the entry, or point it at the test's current id.")

    missing_raises = sorted(k for k in QUARANTINED if not QUARANTINED[k].get("raises"))
    if missing_raises:
        raise pytest.UsageError(
            "tests/quarantine_registry.py entries with no 'raises':\n  "
            + "\n  ".join(missing_raises)
            + "\nA quarantine records ONE known failure. Without the exception "
              "type it immunizes the test against every future failure mode.")

    for key, group in marked.items():
        entry = QUARANTINED[key]
        for item in group:
            item.add_marker(pytest.mark.xfail(
                reason="quarantined: {0}".format(entry["reason"]),
                strict=True,
                # WHY raises= IS NOT OPTIONAL. strict=True only turns an
                # unexpected PASS into a failure; it says nothing about HOW the
                # test fails. Without a type, a production regression that made
                # this test raise TypeError would still be reported as the
                # expected xfail and leave the suite green -- the quarantine
                # would have silently grown from "this one known defect" into
                # "any defect at all", which is the failure mode quarantines
                # are rightly distrusted for.
                raises=entry["raises"],
            ))
