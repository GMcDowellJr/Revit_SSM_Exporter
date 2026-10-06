#!/usr/bin/env python3
"""The exporter's MAJOR.MINOR.PATCH, derived from commit messages; and the PR
check that makes every capture-code commit say whether it breaks analysis.

Nobody edits the version. A breaking change is DECLARED in the commit that
makes it, which is the one judgement no tool can make: a one-line fix to
pixel classification breaks comparability, a large refactor may not.

THE DECLARATION. A commit declares its analysis impact by any one of:
  * a ``!`` after the type: ``fix(grid)!: ...``          -> breaking
  * a ``BREAKING CHANGE: ...`` footer                    -> breaking
  * an ``Analysis-Impact: breaking`` trailer             -> breaking
  * an ``Analysis-Impact: none`` trailer                 -> not breaking
``Analysis-Impact: none`` beside a ``!`` or a BREAKING CHANGE footer
contradicts itself and is refused.

``check --base B --head H``: every non-merge commit in B..H that changes a
file under ``vop_interwoven/`` (the code that runs inside a capture, and the
version file aside) must carry a declaration. A commit whose PARENT does not
yet contain this tool is exempt: the rule applies from the commit that
introduced it, not retroactively. Exit 0 all declared; 1 any undeclared or
contradictory; 2 a git error or bad arguments. The counts are printed, so a
green that checked nothing says so.

``bump``: the last tag ``vX.Y.Z`` reachable from HEAD is the current release.
The commits after it decide the next one: any breaking -> MAJOR; else any
``feat`` -> MINOR; else any ``fix`` / ``perf`` / ``refactor`` -> PATCH; else
no release. With no such tag yet, the version in ``vop_interwoven/_version.py``
is tagged as the BASELINE and nothing is bumped (history before the rule
carries no declarations, so it cannot be read for one). The version file must
equal the tag; if someone edited it by hand, this refuses (exit 2) rather than
guess which is right. ``--write`` rewrites the version file;
``--github-output`` appends ``action`` / ``version`` / ``tag`` for the
workflow. The commit, tag and push are the workflow's.

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

CAPTURE_CODE = "vop_interwoven/"
VERSION_FILE = "vop_interwoven/_version.py"
CHECKER = "tools/analysis_version.py"
TRAILER = "Analysis-Impact"
IMPACTS = ("breaking", "none")
TAG_PATTERN = "v[0-9]*.[0-9]*.[0-9]*"

_HEADER = re.compile(r"^(?P<type>[A-Za-z]+)(\([^)]*\))?(?P<bang>!)?: ")
_BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE: ", re.MULTILINE)
_TRAILER = re.compile(r"^" + TRAILER + r":[ \t]*(?P<value>\S*)[ \t]*$",
                      re.MULTILINE | re.IGNORECASE)
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_VERSION_LINE = re.compile(r'^__version__ = "(?P<v>\d+\.\d+\.\d+)"$', re.MULTILINE)

LEVELS = ("none", "patch", "minor", "major")
PATCH_TYPES = ("fix", "perf", "refactor")


class Refusal(Exception):
    """An input this tool cannot decide; the message says why."""


# --- one commit message ---------------------------------------------------------------

def classify(message):
    """What one commit message declares.

    ``{"type", "breaking", "declared", "impact", "errors", "level"}``:
    ``impact`` is the trailer's value (None when absent); ``errors`` lists
    a malformed or contradictory declaration; ``level`` is the release level
    this commit alone asks for."""
    header = message.splitlines()[0] if message else ""
    m = _HEADER.match(header)
    ctype = m.group("type").lower() if m else None
    bang = bool(m and m.group("bang"))
    footer = bool(_BREAKING_FOOTER.search(message))
    errors = []
    values = [t.group("value").lower() for t in _TRAILER.finditer(message)]
    impact = None
    if len(set(values)) > 1:
        errors.append("{0} given more than once with different values: {1}".format(
            TRAILER, ", ".join(values)))
    elif values:
        impact = values[0]
        if impact not in IMPACTS:
            errors.append("{0}: {1!r} is not one of {2}".format(TRAILER, impact,
                                                                "/".join(IMPACTS)))
            impact = None
    if impact == "none" and (bang or footer):
        errors.append("{0}: none contradicts the commit's own breaking marker ({1})".format(
            TRAILER, "!" if bang else "BREAKING CHANGE footer"))
    breaking = bang or footer or impact == "breaking"
    if breaking:
        level = "major"
    elif ctype == "feat":
        level = "minor"
    elif ctype in PATCH_TYPES:
        level = "patch"
    else:
        level = "none"
    return {"type": ctype, "breaking": breaking, "impact": impact, "errors": errors,
            "declared": bang or footer or impact is not None, "level": level}


def next_version(current, messages):
    """``(version, level, reasons)``: ``current`` raised by the highest level
    any message asks for. ``reasons`` names the headers that set it."""
    m = _SEMVER.match(current or "")
    if not m:
        raise Refusal("current version {0!r} is not MAJOR.MINOR.PATCH".format(current))
    major, minor, patch = (int(x) for x in m.groups())
    found = [(classify(msg)["level"], (msg.splitlines() or [""])[0]) for msg in messages]
    level = max((lv for lv, _h in found), key=LEVELS.index, default="none")
    reasons = [h for lv, h in found if lv == level and level != "none"]
    if level == "major":
        major, minor, patch = major + 1, 0, 0
    elif level == "minor":
        minor, patch = minor + 1, 0
    elif level == "patch":
        patch += 1
    return "{0}.{1}.{2}".format(major, minor, patch), level, reasons


# --- git ------------------------------------------------------------------------------

def _git(repo, *args):
    proc = subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True,
                          text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise Refusal("git {0}: {1}".format(" ".join(args), proc.stderr.strip()))
    return proc.stdout


def _has_path(repo, rev, path):
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e",
                           "{0}:{1}".format(rev, path)],
                          capture_output=True).returncode == 0


def commit_message(repo, sha):
    return _git(repo, "log", "-1", "--format=%B", sha)


def changed_files(repo, sha):
    out = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha)
    return [line for line in out.splitlines() if line]


def touches_capture_code(paths):
    return [p for p in paths if p.startswith(CAPTURE_CODE) and p != VERSION_FILE]


# --- check ----------------------------------------------------------------------------

def check(repo, base, head):
    """``(exit code, report)`` for the commits in base..head."""
    shas = _git(repo, "rev-list", "--no-merges", "--reverse",
                "{0}..{1}".format(base, head)).split()
    report = {"commits": len(shas), "capture_code": 0, "checked": 0, "exempt": 0,
              "declared": 0, "failures": []}
    for sha in shas:
        if not touches_capture_code(changed_files(repo, sha)):
            continue
        report["capture_code"] += 1
        if not _has_path(repo, sha + "^", CHECKER):
            report["exempt"] += 1
            continue
        report["checked"] += 1
        msg = commit_message(repo, sha)
        c = classify(msg)
        problems = list(c["errors"])
        if not c["declared"]:
            problems.append("changes {0} but declares no analysis impact: add "
                            "'{1}: none' or '{1}: breaking' (or mark it '!')".format(
                                CAPTURE_CODE, TRAILER))
        if problems:
            report["failures"].append({"commit": sha[:12],
                                       "header": (msg.splitlines() or [""])[0],
                                       "problems": problems})
        else:
            report["declared"] += 1
    return (1 if report["failures"] else 0), report


# --- bump -----------------------------------------------------------------------------

def read_version_file(path):
    m = _VERSION_LINE.search(Path(path).read_text(encoding="utf-8"))
    if not m:
        raise Refusal("{0} has no line __version__ = \"X.Y.Z\"".format(path))
    return m.group("v")


def write_version_file(path, version):
    text = Path(path).read_text(encoding="utf-8")
    new, n = _VERSION_LINE.subn('__version__ = "{0}"'.format(version), text)
    if n != 1:
        raise Refusal("{0}: expected one __version__ line, found {1}".format(path, n))
    Path(path).write_text(new, encoding="utf-8")


def last_release_tag(repo):
    proc = subprocess.run(["git", "-C", str(repo), "describe", "--tags", "--abbrev=0",
                           "--match", TAG_PATTERN, "HEAD"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    tag = proc.stdout.strip()
    if not _SEMVER.match(tag[1:]):
        raise Refusal("tag {0} matches {1} but is not vMAJOR.MINOR.PATCH".format(
            tag, TAG_PATTERN))
    return tag


def bump(repo, write=False):
    """The release decision for HEAD: ``{"action": "none" | "baseline" |
    "bump", "version", "tag", "level", "reasons", "commits"}``."""
    version_path = Path(repo) / VERSION_FILE
    in_file = read_version_file(version_path)
    tag = last_release_tag(repo)
    if tag is None:
        return {"action": "baseline", "version": in_file, "tag": "v" + in_file,
                "level": "none", "reasons": [], "commits": 0}
    if tag[1:] != in_file:
        raise Refusal("{0} says {1} but the last release tag is {2}; the version file "
                      "is written only by this tool".format(VERSION_FILE, in_file, tag))
    shas = _git(repo, "rev-list", "--no-merges", "{0}..HEAD".format(tag)).split()
    messages = [commit_message(repo, s) for s in shas]
    version, level, reasons = next_version(in_file, messages)
    if level == "none":
        return {"action": "none", "version": in_file, "tag": tag, "level": level,
                "reasons": [], "commits": len(shas)}
    if write:
        write_version_file(version_path, version)
    return {"action": "bump", "version": version, "tag": "v" + version, "level": level,
            "reasons": reasons, "commits": len(shas)}


# --- CLI ------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="every capture-code commit in base..head declares "
                                     "its analysis impact")
    c.add_argument("--base", required=True)
    c.add_argument("--head", required=True)
    c.add_argument("--repo", default=".")
    b = sub.add_parser("bump", help="the next release version from the commits since "
                                    "the last vX.Y.Z tag")
    b.add_argument("--repo", default=".")
    b.add_argument("--write", action="store_true", help="rewrite " + VERSION_FILE)
    b.add_argument("--github-output", default=None,
                   help="append action/version/tag to this file (GITHUB_OUTPUT)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as ex:
        return 2 if ex.code else 0
    try:
        if args.command == "check":
            code, report = check(args.repo, args.base, args.head)
            print("{0} commits in {1}..{2}; {3} change {4}; {5} checked, {6} exempt "
                  "(before this rule), {7} declared, {8} failing".format(
                      report["commits"], args.base[:12], args.head[:12],
                      report["capture_code"], CAPTURE_CODE, report["checked"],
                      report["exempt"], report["declared"], len(report["failures"])))
            for f in report["failures"]:
                for p in f["problems"]:
                    print("FAIL {0} {1!r}: {2}".format(f["commit"], f["header"], p))
            return code
        result = bump(args.repo, write=args.write)
    except Refusal as ex:
        print("REFUSED: {0}".format(ex))
        return 2
    print(json.dumps(result, sort_keys=True))
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            for key in ("action", "version", "tag"):
                handle.write("{0}={1}\n".format(key, result[key]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
