# tests/conftest.py

import os
from pathlib import Path

import pytest

from tests.quarantine_registry import QUARANTINED

_ROOT = Path(__file__).resolve().parents[1]

QUARANTINE_MARKER = (
    "quarantine: a known, recorded failure. Requires a matching entry in "
    "tests/quarantine_registry.py; runs as a STRICT xfail, so fixing the "
    "underlying defect turns the suite red until the entry is removed."
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


def pytest_collection_modifyitems(session, config, items):
    """Bind the quarantine marker to the registry, in BOTH directions.

    A marker without an entry, or an entry without a test, is a hard error
    rather than a skip. Either one on its own is how a quarantine becomes an
    untracked permanent exception -- which is the thing the registry exists
    to prevent, so it cannot itself be allowed to fail quietly.
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
            + "\nAdd an entry stating the reason, the open question and why it "
              "is not a flake, or remove the marker.")

    # Reverse direction, scoped by FILE rather than by how pytest was invoked.
    # An entry is stale only when its file WAS collected and its test was not:
    # that is a rename, a deletion or a dropped marker. A run that simply did
    # not include the file proves nothing, so it is not judged -- otherwise the
    # registry would punish every narrow invocation people actually use.
    #
    # `-k` is excluded outright: it deselects items, and whether it has already
    # run when this hook fires is not ordered, so a deselected test is
    # indistinguishable here from a renamed one.
    if not config.option.keyword:
        collected_files = {Path(i.fspath).resolve().relative_to(_ROOT).as_posix()
                           for i in items}
        stale = sorted(k for k in QUARANTINED
                       if k not in marked and k.split("::", 1)[0] in collected_files)
        if stale:
            raise pytest.UsageError(
                "tests/quarantine_registry.py names test(s) whose file was "
                "collected but which were not -- renamed, deleted, or the "
                "marker was dropped:\n  " + "\n  ".join(stale)
                + "\nRemove the entry, or point it at the test's current id.")

    for key, group in marked.items():
        for item in group:
            item.add_marker(pytest.mark.xfail(
                reason="quarantined: {0}".format(QUARANTINED[key]["reason"]),
                strict=True,
            ))
