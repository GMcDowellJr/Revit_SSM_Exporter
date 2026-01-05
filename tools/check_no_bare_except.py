#!/usr/bin/env python3
"""
Fail CI on bare `except:` in specified files/dirs.

- Scans only *.py.
- Matches Python syntax line:  ^\s*except\s*:\s*(#.*)?$
- Optional whitelist file: lines of "relative/path.py:LINENO"
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import Iterable, List, Set, Tuple


BARE_EXCEPT_RE = re.compile(r"^\s*except\s*:\s*(#.*)?$")


@dataclass(frozen=True)
class Hit:
    path: str
    lineno: int
    line: str


def _iter_py_files(target: str) -> Iterable[str]:
    if os.path.isfile(target):
        if target.endswith(".py"):
            yield target
        return

    # directory
    for root, _, files in os.walk(target):
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(root, fn)


def _load_whitelist(path: str | None) -> Set[Tuple[str, int]]:
    if not path:
        return set()
    if not os.path.exists(path):
        # Treat missing whitelist as empty (safer than silently skipping checks)
        return set()

    allowed: Set[Tuple[str, int]] = set()
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if ":" not in s:
                raise SystemExit(f"Invalid whitelist line (expected path:lineno): {s}")
            p, n = s.rsplit(":", 1)
            try:
                lineno = int(n)
            except ValueError:
                raise SystemExit(f"Invalid whitelist line number: {s}")
            allowed.add((p.replace("\\", "/"), lineno))
    return allowed


def _repo_rel(path: str) -> str:
    # Always normalize to repo-relative unix-style paths for stable CI output.
    rel = os.path.relpath(path, os.getcwd())
    return rel.replace("\\", "/")


def scan(paths: List[str], whitelist: Set[Tuple[str, int]]) -> List[Hit]:
    hits: List[Hit] = []

    expanded: List[str] = []
    for p in paths:
        if not os.path.exists(p):
            raise SystemExit(f"Path not found: {p}")
        expanded.extend(list(_iter_py_files(p)))

    for p in sorted(set(expanded)):
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f, start=1):
                if BARE_EXCEPT_RE.match(line):
                    rel = _repo_rel(p)
                    if (rel, idx) in whitelist:
                        continue
                    hits.append(Hit(path=rel, lineno=idx, line=line.rstrip("\n")))
    return hits


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", nargs="+", required=True, help="Files/dirs to scan")
    ap.add_argument("--whitelist", default=None, help="Optional whitelist file path")
    args = ap.parse_args(argv)

    whitelist = _load_whitelist(args.whitelist)
    hits = scan(args.paths, whitelist)

    if hits:
        print("ERROR: bare `except:` detected (must be `except Exception as e:` or narrower):")
        for h in hits:
            print(f"  {h.path}:{h.lineno}: {h.line.strip()}")
        print("")
        print("Fix: replace `except:` with `except Exception as e:` (and record diagnostics).")
        if args.whitelist:
            print(f"Whitelist file: {args.whitelist} (use path:lineno entries only for legacy blocks).")
        return 2

    print("OK: no bare `except:` found in scanned paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
