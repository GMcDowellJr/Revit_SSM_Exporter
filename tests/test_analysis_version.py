"""tools/analysis_version.py: the PR check and the release bump.

The parser is tested on messages; ``check`` and ``bump`` are tested on REAL
throwaway git repositories, because their correctness is about which commits
git hands them (ranges, merges, tags), which a mocked git would assume rather
than test. Each failing scenario sits beside a passing control in the same
repository, so a check that failed everything would fail a test too.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import vop_interwoven
from tools import analysis_version as av

REPO = Path(__file__).resolve().parent.parent
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


# --- the parser --------------------------------------------------------------------

@pytest.mark.parametrize("message, declared, breaking, level", [
    ("fix(grid): x\n\nAnalysis-Impact: none\n", True, False, "patch"),
    ("fix(grid): x\n\nAnalysis-Impact: breaking\n", True, True, "major"),
    ("fix(grid)!: x\n", True, True, "major"),
    ("feat!: x\n", True, True, "major"),
    ("fix: x\n\nBREAKING CHANGE: grid cells re-classified\n", True, True, "major"),
    ("fix: x\n\nBREAKING-CHANGE: y\n", True, True, "major"),
    ("feat(csv): x\n\nanalysis-impact:   None  \n", True, False, "minor"),
    ("fix(grid): x\n", False, False, "patch"),
    ("feat: x\n", False, False, "minor"),
    ("refactor: x\n\nAnalysis-Impact: none\n", True, False, "patch"),
    ("docs: x\n", False, False, "none"),
    ("Merge pull request #1 from a/b\n\nfeat!: x\n", False, False, "none"),
])
def test_classify(message, declared, breaking, level):
    c = av.classify(message)
    assert (c["declared"], c["breaking"], c["level"], c["errors"]) == (
        declared, breaking, level, [])


@pytest.mark.parametrize("message, fragment", [
    ("fix!: x\n\nAnalysis-Impact: none\n", "contradicts"),
    ("fix: x\n\nBREAKING CHANGE: y\nAnalysis-Impact: none\n", "contradicts"),
    ("fix: x\n\nAnalysis-Impact: maybe\n", "not one of"),
    ("fix: x\n\nAnalysis-Impact: none\nAnalysis-Impact: breaking\n", "more than once"),
])
def test_classify_refuses_malformed_declarations(message, fragment):
    assert any(fragment in e for e in av.classify(message)["errors"])


def test_breaking_mentioned_in_prose_is_not_a_footer():
    c = av.classify("fix: x\n\nThis is not a BREAKING CHANGE: just prose.\n")
    assert not c["breaking"] and not c["declared"]


@pytest.mark.parametrize("messages, expected", [
    ([], ("1.4.2", "none")),
    (["docs: a", "test: b"], ("1.4.2", "none")),
    (["fix: a"], ("1.4.3", "patch")),
    (["fix: a", "feat: b"], ("1.5.0", "minor")),
    (["feat: b", "fix(x)!: c", "fix: a"], ("2.0.0", "major")),
    (["fix: a\n\nAnalysis-Impact: breaking"], ("2.0.0", "major")),
])
def test_next_version(messages, expected):
    version, level, reasons = av.next_version("1.4.2", messages)
    assert (version, level) == expected
    assert bool(reasons) == (level != "none")


def test_next_version_refuses_a_non_semver_current():
    with pytest.raises(av.Refusal):
        av.next_version("vop_interwoven", ["fix: a"])


# --- a throwaway repository ----------------------------------------------------------

def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args), check=True, env=GIT_ENV,
                          capture_output=True, text=True).stdout.strip()


def commit(repo, message, files):
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        git(repo, "add", rel)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    """A repository whose first commit predates the rule and whose second
    introduces it: the real tool is copied in, and the version file is
    PINNED at 1.0.0. Copying the live _version.py made every bump test
    depend on the current release -- main went red at its own v1.1.0."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit(r, "feat: before the rule", {"vop_interwoven/a.py": "A = 1\n"})
    commit(r, "chore: add the rule", {
        av.CHECKER: (REPO / av.CHECKER).read_text(encoding="utf-8"),
        av.VERSION_FILE: '__version__ = "1.0.0"\n'})
    return r


# --- check ------------------------------------------------------------------------------

def test_check_flags_only_undeclared_capture_code_commits(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "fix(grid): declared\n\nAnalysis-Impact: none",
           {"vop_interwoven/a.py": "A = 2\n"})
    commit(repo, "fix(grid)!: breaking", {"vop_interwoven/a.py": "A = 3\n"})
    bad = commit(repo, "fix(grid): undeclared", {"vop_interwoven/core/b.py": "B = 1\n"})
    commit(repo, "docs: outside capture code", {"tools/README.md": "x\n"})
    commit(repo, "chore(release): v9.9.9", {av.VERSION_FILE: '__version__ = "9.9.9"\n'})
    code, report = av.check(repo, base, "HEAD")
    assert code == 1
    assert (report["commits"], report["capture_code"], report["checked"],
            report["declared"]) == (5, 3, 3, 2)
    assert [f["commit"] for f in report["failures"]] == [bad[:12]]


def test_check_control_all_declared_is_green(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "fix: a\n\nAnalysis-Impact: none", {"vop_interwoven/a.py": "A = 2\n"})
    commit(repo, "feat: b\n\nAnalysis-Impact: breaking", {"vop_interwoven/a.py": "A = 3\n"})
    code, report = av.check(repo, base, "HEAD")
    assert (code, report["checked"], report["declared"]) == (0, 2, 2)


def test_check_refuses_a_contradiction(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "fix!: x\n\nAnalysis-Impact: none", {"vop_interwoven/a.py": "A = 2\n"})
    code, report = av.check(repo, base, "HEAD")
    assert code == 1 and "contradicts" in report["failures"][0]["problems"][0]


def test_commits_before_the_rule_are_exempt(repo):
    root = git(repo, "rev-list", "--max-parents=0", "HEAD")
    code, report = av.check(repo, root, "HEAD")                    # the rule's own commit
    assert code == 0
    first = git(repo, "rev-list", "--reverse", "HEAD").split()[0]
    git(repo, "checkout", "-q", "-b", "old", first)
    commit(repo, "fix: undeclared, on a branch from before the rule",
           {"vop_interwoven/a.py": "A = 5\n"})
    code, report = av.check(repo, first, "HEAD")
    assert (code, report["exempt"], report["checked"]) == (0, 1, 0)


def test_merge_commits_are_not_checked_their_parents_are(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "fix: on the branch\n\nAnalysis-Impact: none",
           {"vop_interwoven/a.py": "A = 7\n"})
    git(repo, "checkout", "-q", "main")
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "docs: main moves", {"README.md": "x\n"})
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge branch 'feature'", "feature")
    code, report = av.check(repo, base, "HEAD")
    assert (code, report["commits"], report["checked"]) == (0, 2, 1)


def test_cli_exit_codes(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "fix: undeclared", {"vop_interwoven/a.py": "A = 9\n"})
    tool = [os.sys.executable, str(repo / av.CHECKER)]
    assert subprocess.run(tool + ["check", "--repo", str(repo), "--base", base,
                                  "--head", "HEAD"], capture_output=True).returncode == 1
    assert subprocess.run(tool + ["check", "--repo", str(repo), "--base", "nope",
                                  "--head", "HEAD"], capture_output=True).returncode == 2


# --- bump -------------------------------------------------------------------------------

def test_first_run_tags_the_baseline_without_bumping(repo):
    commit(repo, "feat!: history before the tag is never read",
           {"vop_interwoven/a.py": "A = 2\n"})
    result = av.bump(repo, write=True)
    assert result["action"] == "baseline"
    assert result["version"] == av.read_version_file(repo / av.VERSION_FILE)


@pytest.mark.parametrize("messages, expected, level", [
    (["docs: a"], None, "none"),
    (["fix: a\n\nAnalysis-Impact: none"], "1.0.1", "patch"),
    (["fix: a\n\nAnalysis-Impact: none", "feat: b\n\nAnalysis-Impact: none"], "1.1.0",
     "minor"),
    (["feat: b\n\nAnalysis-Impact: none", "fix: c\n\nAnalysis-Impact: breaking"], "2.0.0",
     "major"),
])
def test_bump_from_the_last_tag(repo, messages, expected, level):
    assert av.read_version_file(repo / av.VERSION_FILE) == "1.0.0"
    git(repo, "tag", "v1.0.0")
    for n, msg in enumerate(messages):
        commit(repo, msg, {"vop_interwoven/m{0}.py".format(n): "X = 1\n"})
    result = av.bump(repo, write=True)
    assert result["level"] == level
    on_disk = av.read_version_file(repo / av.VERSION_FILE)
    if expected is None:
        assert result["action"] == "none" and on_disk == "1.0.0"
    else:
        assert result["action"] == "bump" and result["tag"] == "v" + expected
        assert on_disk == expected                                    # the FILE, not the return


def test_bump_refuses_a_hand_edited_version_file(repo):
    git(repo, "tag", "v" + av.read_version_file(repo / av.VERSION_FILE))
    av.write_version_file(repo / av.VERSION_FILE, "7.7.7")
    commit(repo, "fix: hand edit\n\nAnalysis-Impact: none",
           {av.VERSION_FILE: (repo / av.VERSION_FILE).read_text()})
    with pytest.raises(av.Refusal, match="last release tag"):
        av.bump(repo)


def test_unrelated_tags_are_not_releases(repo):
    git(repo, "tag", "baseline/v1-freeze")                 # the shape of tags already on origin
    git(repo, "tag", "pre-image-based-retooling")
    assert av.bump(repo)["action"] == "baseline"


def test_the_package_version_is_the_version_file():
    """The file the tool writes is the one the package and run_meta read."""
    assert av.read_version_file(REPO / av.VERSION_FILE) == vop_interwoven.__version__
