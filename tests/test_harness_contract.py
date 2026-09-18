"""The quarantine harness, driven from OUTSIDE pytest.

WHY THIS CANNOT BE AN ORDINARY TEST
-----------------------------------
`tests/conftest.py` decides whether failures are reported at all. A test
running inside that harness is subject to it, so it cannot observe it: a
harness that wrongly swallowed every failure would swallow this file's
failures too and report a green suite. The only way to check "does a failure
get REPORTED" is to run pytest as a subprocess and look at its exit code.

That blind spot is not hypothetical. Two defects shipped in this harness and
both were invisible to the 959-test suite it governs:

  1. `xfail(strict=True)` with no `raises` absorbed ANY exception. strict only
     turns an unexpected PASS into a failure; it says nothing about HOW a test
     fails. A production regression making a quarantined test raise TypeError
     would have been reported as the expected xfail, green.
  2. The reverse registry check made a quarantined FILE unrunnable by node id:
     `pytest <file>::<other_test>` collects the file with the quarantined
     sibling absent, which at that hook is indistinguishable from a rename.

Each scenario below mutates a COPY of the repo, runs pytest in it, and asserts
on the outcome. Nothing here touches the working tree.

DELIBERATELY NEVER RUNS THE FULL INNER SUITE. Every inner invocation names
specific files, so the copied tree's own copy of this file is never collected
and there is no recursion.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache", "graphify-out",
    "archive", "legacy", "campaign", "examples", "docs",
)

QUARANTINED_TEST = (
    "tests/test_occlusion_contract.py::"
    "test_rasterize_areal_loops_low_does_not_populate_rect_gate_cells")
SIBLING_TEST = (
    "tests/test_occlusion_contract.py::test_areal_high_silhouette_writes_w_occ")


@pytest.fixture
def repo():
    """A throwaway copy of the repo, with a helper to run pytest inside it."""
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "repo"
        shutil.copytree(ROOT, work, ignore=_IGNORE)

        class _Repo(object):
            path = work

            @staticmethod
            def run(*args):
                p = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
                    + list(args),
                    cwd=str(work), capture_output=True, text=True, timeout=600)
                return p.returncode, re.sub(r"\s+", " ", p.stdout + p.stderr)

            @staticmethod
            def edit(rel, old, new, count=1):
                f = work / rel
                src = f.read_text(encoding="utf-8")
                assert old in src, "anchor not found in {0}: {1!r}".format(rel, old[:60])
                f.write_text(src.replace(old, new, count), encoding="utf-8")

        yield _Repo


# --------------------------------------------------------------------------
# The two defects that shipped
# --------------------------------------------------------------------------

def test_a_quarantined_file_is_still_runnable_by_node_id(repo):
    """Defect 2. Selecting an unquarantined sibling raised UsageError before
    the selected test could run, making the whole file unrunnable one test at
    a time."""
    rc, out = repo.run(SIBLING_TEST)
    assert rc == 0, out[-400:]
    assert "1 passed" in out, out[-400:]


def test_a_new_failure_mode_is_reported_not_absorbed(repo):
    """Defect 1. The quarantine records ONE known failure; anything else the
    test raises is new and must reach the report."""
    repo.edit(
        "tests/test_occlusion_contract.py",
        "def test_rasterize_areal_loops_low_does_not_populate_rect_gate_cells():\n",
        "def test_rasterize_areal_loops_low_does_not_populate_rect_gate_cells():\n"
        "    raise TypeError('simulated production regression')\n")
    rc, out = repo.run("tests/test_occlusion_contract.py")
    assert rc != 0, "a TypeError in a quarantined test was absorbed: " + out[-400:]


# --------------------------------------------------------------------------
# The properties the harness is supposed to have
# --------------------------------------------------------------------------

def test_a_quarantined_test_that_starts_passing_turns_the_suite_red(repo):
    """strict=True. The day the underlying defect is fixed the suite must go
    red and name the entry to remove -- otherwise a quarantine outlives the
    bug it was recording, which is how quarantines become permanent."""
    repo.edit(
        "tests/test_occlusion_contract.py",
        '    assert out_cells == set()\n'
        '    assert all(value == float("inf") for value in r.w_occ)\n',
        "    assert True  # defect fixed; the test now passes\n")
    rc, out = repo.run("tests/test_occlusion_contract.py")
    assert rc != 0, "a quarantined test started passing and stayed green: " + out[-400:]


def test_a_dropped_marker_is_caught_from_an_unrelated_narrow_run(repo):
    """The reverse binding reads the FILE, so it does not depend on the run
    having collected it. A marker removed while the registry still names the
    test is a stale entry however pytest was invoked."""
    repo.edit("tests/test_occlusion_contract.py",
              "@pytest.mark.quarantine  # see tests/quarantine_registry.py\n", "")
    rc, out = repo.run("tests/test_geometry.py")
    assert rc != 0, out[-400:]
    assert "quarantine_registry" in out, out[-400:]


def test_a_renamed_quarantined_test_is_caught(repo):
    """Both directions fire: the marker now has no entry, and the entry now
    names no test."""
    repo.edit(
        "tests/test_occlusion_contract.py",
        "def test_rasterize_areal_loops_low_does_not_populate_rect_gate_cells():",
        "def test_rasterize_areal_loops_low_does_not_populate_rect_gate_cellsRENAMED():")
    rc, out = repo.run("tests/test_occlusion_contract.py")
    assert rc != 0, out[-400:]
    assert "quarantine_registry" in out, out[-400:]


def test_a_marker_with_no_registry_entry_is_an_error(repo):
    """A bare marker is the untracked exception the registry exists to
    replace, so it must not simply work."""
    # A NEW file rather than a mutation of an existing one. The first version
    # of this test injected a top-level decorator into tests/test_geometry.py,
    # whose tests are METHODS -- producing an IndentationError that satisfied
    # `rc != 0` for entirely the wrong reason. Asserting on the message is what
    # caught it; writing a file whose shape is known removes the trap.
    (repo.path / "tests" / "test_bare_marker_probe.py").write_text(
        "import pytest\n\n\n"
        "@pytest.mark.quarantine\n"
        "def test_marked_but_unregistered():\n"
        "    assert True\n",
        encoding="utf-8")
    rc, out = repo.run("tests/test_bare_marker_probe.py")
    assert rc != 0, out[-400:]
    assert "no entry in" in out, out[-400:]


def test_a_registry_entry_without_raises_is_an_error(repo):
    """Without the exception type the quarantine is unbounded, which is
    defect 1 by another route -- so it is refused at collection."""
    repo.edit("tests/quarantine_registry.py", '        "raises": AssertionError,\n', "")
    rc, out = repo.run("tests/test_occlusion_contract.py")
    assert rc != 0, out[-400:]
    assert "raises" in out, out[-400:]


def test_the_unmodified_copy_is_green(repo):
    """The control. Every scenario above asserts a NON-zero exit code, so all
    of them would pass against a harness that was broken outright. This is
    what distinguishes "the mutation was detected" from "nothing works"."""
    rc, out = repo.run("tests/test_occlusion_contract.py")
    assert rc == 0, out[-400:]
    assert "1 xfailed" in out, out[-400:]
