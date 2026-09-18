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


def _perfect_matching(span_candidates, n_handlers):
    """Kuhn's augmenting-path matching: assign each span a DISTINCT handler it
    contains. Returns {handler_index: span_index}.

    Coverage alone is not set equality, which is the whole point of this
    function. One fabricated span per file, spanning that file's first
    discarding handler to its last, contains every handler in the file -- so a
    coverage check certifies 29 spans as accounting for all 179, and an
    arbitrarily overbroad rule passes. Requiring an INJECTIVE assignment kills
    that: 29 spans cannot be matched to 179 distinct handlers, whatever they
    contain.

    Matching rather than a positional heuristic because neither side is
    one-line. Semgrep anchors a match on the `try`, so 9 of the real spans
    contain more than one discarding handler, and in entry_dynamo.py a span
    ends on a sibling handler's closing paren rather than on its own `pass`.
    Both are legitimate. A bijection has to exist, not be guessable.
    """
    match_h = {}

    def augment(span, seen):
        for h in span_candidates[span]:
            if h in seen:
                continue
            seen.add(h)
            if h not in match_h or augment(match_h[h], seen):
                match_h[h] = span
                return True
        return False

    for span in range(len(span_candidates)):
        augment(span, set())
    return match_h


def reconcile(rows, semgrep_json):
    """Prove semgrep's spans and the AST's handlers are the SAME SET.

    Fails on any of: a handler no span covers (the rule is blind to a shape),
    a span covering no handler (the rule matches something that is not one),
    or the absence of a one-to-one assignment between them (the rule is
    overbroad, or double-reports). Equal counts are checked too, but last --
    two different populations of 179 both have 179 members.
    """
    data = json.loads(Path(semgrep_json).read_text(encoding="utf-8"))
    spans = []
    for result in data.get("results", []):
        if "discarded" not in result["check_id"]:
            continue
        spans.append((result["path"], result["start"]["line"], result["end"]["line"]))

    handlers = [(path, lineno) for path, lineno, _shape, _body in rows]
    index_of = {h: i for i, h in enumerate(handlers)}

    # Which handlers could each span be accounting for?
    by_file = collections.defaultdict(list)
    for handler in handlers:
        by_file[handler[0]].append(handler)
    candidates = [
        [index_of[h] for h in by_file.get(path, []) if lo <= h[1] <= hi]
        for path, lo, hi in spans
    ]

    spurious = [spans[i] for i, c in enumerate(candidates) if not c]
    uncovered = [h for i, h in enumerate(handlers)
                 if not any(i in c for c in candidates)]
    matched = _perfect_matching(candidates, len(handlers))
    unmatched_spans = [spans[i] for i in range(len(spans))
                       if i not in set(matched.values())]
    unmatched_handlers = [handlers[i] for i in range(len(handlers))
                          if i not in matched]

    print("AST ground truth handlers : %d" % len(handlers))
    print("semgrep match spans       : %d" % len(spans))
    print("handlers no span covers   : %d" % len(uncovered))
    print("spans covering no handler : %d" % len(spurious))
    print("handlers left unmatched   : %d" % len(unmatched_handlers))
    print("spans left unmatched      : %d" % len(unmatched_spans))

    for path, lineno in sorted(uncovered)[:20]:
        print("    UNCOVERED HANDLER %s:%d" % (path, lineno))
    for path, lo, hi in sorted(spurious)[:20]:
        print("    SPURIOUS SPAN     %s:%d-%d" % (path, lo, hi))
    for path, lineno in sorted(unmatched_handlers)[:20]:
        print("    UNMATCHED HANDLER %s:%d" % (path, lineno))
    for path, lo, hi in sorted(unmatched_spans)[:20]:
        print("    UNMATCHED SPAN    %s:%d-%d" % (path, lo, hi))

    ok = not (uncovered or spurious or unmatched_handlers or unmatched_spans)
    print()
    print("BIJECTION: %s" % ("PROVEN -- the two are the same set"
                             if ok else "NOT PROVEN"))
    return 0 if ok else 1


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
