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

WHAT `--reconcile` PROVES, AND THE TWO WEAKER THINGS IT USED TO PROVE
---------------------------------------------------------------------
Three versions, each defeated by a concrete attack rather than by argument:

1. COVERAGE -- "every handler falls inside some span". Defeated by one
   fabricated span per file, that file's first discarding handler to its
   last: 29 spans certified as accounting for all 179, exit 0.

2. COVERAGE + A PERFECT MATCHING -- "assign each span a distinct handler it
   contains". Defeated by repeating each file's whole-file span once per
   handler: 179 spans, a perfect matching exists, exit 0. Cardinality and
   coverage, but no span IDENTIFIES anything.

3. IDENTIFICATION (this version). A legitimate match anchors on a `try`
   statement, so its span must equal that statement's EXACT extent
   (first line and last line both). Verified: all 179 real spans do, and
   each such `try` carries exactly one discarding handler. A span that is
   not some `try`'s exact extent identifies nothing and is rejected, which
   is what both fabrications are. The injective matching is kept for the
   case of a `try` with two discarding handlers -- none exists today, and
   the guarantee should not depend on that staying true.

Positional shortcuts were tried first and do not work. Semgrep reports the
span of the whole `try` statement, INCLUDING handlers after the matched one,
so "the span ends on its own handler's body" holds for 178 of 179 and fails
on `vop_interwoven/entry_dynamo.py`, whose `except ImportError: pass` is
followed by a longer sibling handler. `extra.metavars` is empty in this
semgrep version's output, so the bound exception type is not available either.

A PARSE FAILURE IS A FAILURE. A file this tool cannot parse is silently
absent from the ground truth, and a validator that drops what it cannot read
certifies exactly the code it failed to inspect. It is reported and it fails
the run. This repo targets IronPython 2 and CPython 3 both, so a file using
syntax the running interpreter rejects is a live possibility, not a
hypothetical.

Usage:
    python tools/count_discarded_handlers.py vop_interwoven tools
    semgrep --config .semgrep/vop-rules.yml vop_interwoven tools --json -q > /tmp/sg.json
    python tools/count_discarded_handlers.py vop_interwoven tools --reconcile /tmp/sg.json

Exit status: 0 on success; 1 from --reconcile when the bijection is not
proven; 2 when any scanned file could not be parsed.
"""
import argparse
import ast
import collections
import json
import sys
from pathlib import Path


def handler_shape(handler):
    """Name the syntactic shape, because that is what a pattern is blind to.

    TUPLE ARITY IS PART OF THE SHAPE. `.semgrep/vop-rules.yml` enumerates tuple
    arities one pattern at a time -- a tuple metavariable does not bind a
    variadic exception list -- so a 4-tuple is invisible to a rule that stops at
    3. Reporting every tuple as "except (A, B):" would hide exactly the axis the
    rule is sensitive to, so the arity is spelled out and a new one shows up
    here before CI has to fail to find it.
    """
    if handler.type is None:
        return "bare `except:`"
    if isinstance(handler.type, ast.Tuple):
        n = len(handler.type.elts)
        names = ", ".join(chr(ord("A") + i) for i in range(n)) if n <= 6 else "%d-tuple" % n
        return "except (%s) as e:" % names if handler.name else "except (%s):" % names
    return "except X as e:" if handler.name else "except X:"


def _discards(handler):
    return len(handler.body) == 1 and isinstance(handler.body[0], (ast.Pass, ast.Continue))


def walk(roots):
    """Return (handlers, try_extents, parse_failures, scanned).

    handlers      -- [(path, lineno, shape, body_kind)] for every handler whose
                     body is exactly `pass` or `continue`.
    try_extents   -- {path: {(first_line, last_line): [handler_lineno, ...]}}
                     for each `try` carrying at least one such handler. This is
                     what a semgrep match is allowed to identify.
    parse_failures-- [(path, reason)]; never silently dropped.
    scanned       -- how many .py files were actually read.

    A ROOT THAT DOES NOT EXIST IS A HARD ERROR. `Path.rglob` on a missing
    directory yields nothing and raises nothing, so a misspelled or deleted
    scan root used to produce an empty ground truth that reconciled against an
    empty semgrep result and printed BIJECTION: PROVEN. A typo in the CI
    workflow's paths would have certified a scan of nothing.
    """
    missing = [str(r) for r in roots if not r.exists()]
    if missing:
        raise ValueError("scan root does not exist: %s" % ", ".join(missing))

    handlers = []
    try_extents = collections.defaultdict(dict)
    parse_failures = []
    scanned = 0

    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            scanned += 1
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError, OSError) as exc:
                parse_failures.append((str(path), "%s: %s" % (type(exc).__name__, exc)))
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Try):
                    discarding = [h.lineno for h in node.handlers if _discards(h)]
                    if discarding:
                        try_extents[str(path)][(node.lineno, node.end_lineno)] = discarding
                if isinstance(node, ast.ExceptHandler) and _discards(node):
                    handlers.append((str(path), node.lineno, handler_shape(node),
                                     type(node.body[0]).__name__.lower()))
    return handlers, try_extents, parse_failures, scanned


def report(handlers):
    shapes = collections.Counter(h[2] for h in handlers)
    print("=== handlers that discard the exception (body is exactly pass/continue) ===")
    for shape, n in sorted(shapes.items(), key=lambda kv: -kv[1]):
        print("  %-22s %4d" % (shape, n))
    print("  %-22s %4d" % ("TOTAL", len(handlers)))
    print()
    print("  by body:", dict(collections.Counter(h[3] for h in handlers)))
    print()
    per_file = collections.Counter(h[0] for h in handlers)
    print("=== per file (%d files carry at least one) ===" % len(per_file))
    cumulative = 0
    for path, n in per_file.most_common():
        cumulative += n
        print("  %4d  %5.1f%%  %s" % (n, 100.0 * cumulative / max(1, len(handlers)), path))


def _perfect_matching(candidates):
    """Kuhn's augmenting path: give each span a DISTINCT handler it identifies."""
    match_handler = {}

    def augment(span, seen):
        for handler in candidates[span]:
            if handler in seen:
                continue
            seen.add(handler)
            if handler not in match_handler or augment(match_handler[handler], seen):
                match_handler[handler] = span
                return True
        return False

    for span in range(len(candidates)):
        augment(span, set())
    return match_handler


def reconcile(handlers, try_extents, semgrep_json):
    """Prove semgrep's spans and the AST's handlers are the SAME SET."""
    data = json.loads(Path(semgrep_json).read_text(encoding="utf-8"))
    spans = [(r["path"], r["start"]["line"], r["end"]["line"])
             for r in data.get("results", [])
             if "discarded" in r["check_id"]]

    # A `try` carrying MORE THAN ONE discarding handler cannot be identified
    # from a span at all: every span for it is that statement's extent, so two
    # identical spans match two handlers arbitrarily and nothing establishes
    # which is which. Two duplicate copies of one span would then certify a
    # `try` whose second handler the rule never matched.
    #
    # There are none today (measured: 179 such `try` statements, all with
    # exactly one). Rather than let the guarantee quietly depend on that, this
    # refuses to certify when one appears and says what would have to change.
    # Refusing to prove what cannot be proven is the point of the file.
    ambiguous = [(path, extent, lines)
                 for path, extents in try_extents.items()
                 for extent, lines in extents.items() if len(lines) > 1]
    if ambiguous:
        for path, (first, last), lines in sorted(ambiguous)[:20]:
            print("AMBIGUOUS %s:%d-%d carries %d discarding handlers (lines %s); "
                  "a span cannot identify which."
                  % (path, first, last, len(lines),
                     ", ".join(str(n) for n in lines)), file=sys.stderr)
        print("REFUSING: %d `try` statement(s) carry more than one discarding "
              "handler. Span-based identification cannot distinguish them, so "
              "no bijection can be proven. Carry handler-specific identity "
              "(a metavariable binding, or a per-handler rule) before this can "
              "certify them." % len(ambiguous), file=sys.stderr)
        return 2

    index_of = {(p, ln): i for i, (p, ln, _s, _b) in enumerate(handlers)}

    # A span may only identify handlers of the `try` whose extent it EXACTLY is.
    candidates = []
    unanchored = []
    for path, first, last in spans:
        owned = try_extents.get(path, {}).get((first, last))
        if owned is None:
            unanchored.append((path, first, last))
            candidates.append([])
        else:
            candidates.append([index_of[(path, ln)] for ln in owned])

    matched = _perfect_matching(candidates)
    unmatched_spans = [spans[i] for i in range(len(spans)) if i not in set(matched.values())]
    unmatched_handlers = [handlers[i][:2] for i in range(len(handlers)) if i not in matched]

    print("AST ground truth handlers      : %d" % len(handlers))
    print("semgrep match spans            : %d" % len(spans))
    print("spans not a try's exact extent : %d" % len(unanchored))
    print("handlers left unidentified     : %d" % len(unmatched_handlers))
    print("spans left unmatched           : %d" % len(unmatched_spans))

    for path, first, last in sorted(unanchored)[:20]:
        print("    UNANCHORED SPAN   %s:%d-%d  (identifies no try statement)" % (path, first, last))
    for path, lineno in sorted(unmatched_handlers)[:20]:
        print("    UNIDENTIFIED      %s:%d" % (path, lineno))
    for path, first, last in sorted(unmatched_spans)[:20]:
        print("    UNMATCHED SPAN    %s:%d-%d" % (path, first, last))

    ok = not (unanchored or unmatched_handlers or unmatched_spans)
    print()
    print("BIJECTION: %s" % ("PROVEN -- every span identifies one handler, and every "
                             "handler one span" if ok else "NOT PROVEN"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", help="files or directories to walk")
    ap.add_argument("--reconcile", metavar="SEMGREP_JSON",
                    help="semgrep --json output to prove a bijection against this walk")
    args = ap.parse_args(argv)

    try:
        handlers, try_extents, parse_failures, scanned = walk([Path(p) for p in args.paths])
    except ValueError as exc:
        print("REFUSING: %s" % exc, file=sys.stderr)
        return 2

    if scanned == 0:
        print("REFUSING: the given paths matched no .py files, so there is "
              "nothing to certify. Paths given: %s" % ", ".join(args.paths),
              file=sys.stderr)
        return 2

    if parse_failures:
        # Never a warning. A file that could not be read is a file this tool
        # has no ground truth for, and reporting success would certify it.
        for path, reason in parse_failures:
            print("PARSE FAILURE %s: %s" % (path, reason), file=sys.stderr)
        print("REFUSING: %d file(s) could not be parsed; the ground truth is "
              "incomplete and nothing can be certified against it." % len(parse_failures),
              file=sys.stderr)
        return 2

    print("scanned %d .py file(s) under: %s\n" % (scanned, ", ".join(args.paths)))
    if args.reconcile:
        return reconcile(handlers, try_extents, args.reconcile)
    report(handlers)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # Piped into `head` or similar. Redirect stdout to devnull so the
        # interpreter's shutdown flush cannot raise a second time, then exit
        # on SIGPIPE's conventional status. The consumer closing the pipe IS
        # the intended end of the run, which is why this one is safe.
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(141)
