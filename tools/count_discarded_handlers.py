#!/usr/bin/env python
"""Independent AST ground truth for handlers that discard their exception.

WHY THIS EXISTS, AND WHY IT IS NOT THE SEMGREP RULE
---------------------------------------------------
`.semgrep/vop-rules.yml` reports a count. A count is a claim about what the
PATTERN matched, never about the repo. That distinction is not academic here:
the `except X: pass` sweep was first reported as **86** and proposed as a
ratchet baseline, when the real population is **179** -- semgrep's
`except $E:` matches neither `except X as e:` (90 here) nor `except (A, B):`
(3). Baselining at 86 would have grandfathered 93 live Refactor Rule #1
violations under a check reporting itself clean.

An AST walk cannot have a blind spot of that kind, because it enumerates
handlers rather than matching shapes. So this is the number, and the semgrep
rule is checked AGAINST it -- not the other way round.

EQUAL COUNTS ARE NOT THE PROOF; EQUAL SETS ARE. Two different populations of
179 would pass a count check. `--reconcile` takes semgrep's JSON and matches
each match span against each handler's line, reporting what was MISSED.

Usage:
    python tools/count_discarded_handlers.py vop_interwoven tools
    semgrep --config .semgrep/vop-rules.yml vop_interwoven tools --json -q > /tmp/sg.json
    python tools/count_discarded_handlers.py vop_interwoven tools --reconcile /tmp/sg.json

Exit status is 0 when reporting, and 1 from --reconcile when the rule misses
any handler the AST found.
"""
import argparse
import ast
import collections
import json
import sys
from pathlib import Path


def handler_shape(handler):
    """Name the syntactic shape, because that is what a pattern is blind to."""
    if handler.type is None:
        return "bare `except:`"
    if isinstance(handler.type, ast.Tuple):
        return "except (A, B) as e:" if handler.name else "except (A, B):"
    return "except X as e:" if handler.name else "except X:"


def discarding_handlers(roots):
    """Yield (path, lineno, shape, body) for every handler whose body is
    exactly `pass` or `continue` -- the exception is bound or named and then
    thrown away, which Refactor Rule #1 forbids."""
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError) as exc:
                print("SKIP (unparseable) %s: %s" % (path, exc), file=sys.stderr)
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                if len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue)):
                    yield (str(path), node.lineno, handler_shape(node),
                           type(node.body[0]).__name__.lower())


def report(rows):
    shapes = collections.Counter(r[2] for r in rows)
    print("=== handlers that discard the exception (body is exactly pass/continue) ===")
    for shape, n in sorted(shapes.items(), key=lambda kv: -kv[1]):
        print("  %-22s %4d" % (shape, n))
    print("  %-22s %4d" % ("TOTAL", len(rows)))
    print()
    print("  by body:", dict(collections.Counter(r[3] for r in rows)))
    print()
    per_file = collections.Counter(r[0] for r in rows)
    print("=== per file (%d files carry at least one) ===" % len(per_file))
    cumulative = 0
    for path, n in per_file.most_common():
        cumulative += n
        print("  %4d  %5.1f%%  %s" % (n, 100.0 * cumulative / max(1, len(rows)), path))


def reconcile(rows, semgrep_json):
    """Match semgrep's spans against the AST's handlers and report MISSES."""
    data = json.loads(Path(semgrep_json).read_text(encoding="utf-8"))
    spans = collections.defaultdict(list)
    for result in data.get("results", []):
        if "discarded" not in result["check_id"]:
            continue
        spans[result["path"]].append((result["start"]["line"], result["end"]["line"]))

    missed = [(p, ln) for p, ln, _s, _b in rows
              if not any(lo <= ln <= hi for lo, hi in spans.get(p, []))]
    total_spans = sum(len(v) for v in spans.values())

    print("AST ground truth handlers : %d" % len(rows))
    print("semgrep match spans       : %d" % total_spans)
    print("covered                   : %d" % (len(rows) - len(missed)))
    print("MISSED                    : %d" % len(missed))
    for path, ln in sorted(missed):
        print("    MISS %s:%d" % (path, ln))
    return 1 if missed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", help="files or directories to walk")
    ap.add_argument("--reconcile", metavar="SEMGREP_JSON",
                    help="semgrep --json output to check FOR MISSES against this walk")
    args = ap.parse_args(argv)

    rows = list(discarding_handlers([Path(p) for p in args.paths]))
    if args.reconcile:
        return reconcile(rows, args.reconcile)
    report(rows)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # Piped into `head` or similar. Redirect stdout to devnull so the
        # interpreter's shutdown flush cannot raise a second time, then exit
        # on SIGPIPE's conventional status. Recorded rather than swallowed:
        # this is the one recovery here that is unambiguously safe, because
        # the consumer closing the pipe IS the intended end of the run.
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(141)
