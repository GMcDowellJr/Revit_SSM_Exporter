#!/usr/bin/env python3
"""
gen_maps.py

Generate navigation artifacts for navigation-first, source-gated debugging.

Run this script FROM the root/tools

Outputs (in the current working directory):
- vop_interwoven_code_map_authoritative.md
- vop_interwoven_trace_map.md
- vop_interwoven_symbol_index.md

Design goals:
- Deterministic outputs (stable ordering; no timestamps)
- No third-party dependencies
- Scoped to this folder only (no repo-wide scanning)
"""

from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple


OUT_CODE_MAP = "vop_interwoven_code_map_authoritative.md"
OUT_TRACE_MAP = "vop_interwoven_trace_map.md"
OUT_SYMBOL_INDEX = "vop_interwoven_symbol_index.md"

# Exclude patterns within the vop_interwoven folder.
# Tweak these if you introduce explicit legacy/archive subfolders.
EXCLUDE_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".git",
    ".github",
    "build",
    "dist",
    "venv",
    ".venv",
    "env",
    ".env",
    # legacy/archive suggestions:
    "legacy",
    "archive",
    "_legacy",
    "_archive",
}

EXCLUDE_FILES = {
    "__init__.py",  # usually low-signal; keep if you rely on it for exports
}

# “High-signal” symbols we want to surface early in the symbol index & trace map.
HIGH_SIGNAL = [
    "run_vop_pipeline",
    "run_vop_pipeline_with_png",
    "run_vop_pipeline_with_csv",
    "run_vop_pipeline_streaming",
    "run_pipeline_from_dynamo_input",
    "process_document_views",
    "process_document_views_streaming",
    "render_model_front_to_back",
    "init_view_raster",
    "_view_signature",
    "resolve_view_bounds",
    "resolve_annotation_only_bounds",
    "rasterize_annotations",
    "collect_view_elements",
    "get_element_silhouette",
]


@dataclass(frozen=True)
class DefInfo:
    name: str
    kind: str  # "function" | "class" | "method"
    file_rel: str
    lineno: int


def _is_excluded_dir(dirname: str) -> bool:
    return dirname in EXCLUDE_DIRS or dirname.startswith(".")


def iter_py_files(root_dir: str) -> List[str]:
    """
    Return a stable-sorted list of .py file paths under root_dir,
    excluding common cache/venv/legacy/archive folders.
    """
    out: List[str] = []
    for cur, dirs, files in os.walk(root_dir):
        # mutate dirs in place to prune walk
        dirs[:] = [d for d in dirs if not _is_excluded_dir(d)]
        for f in files:
            if not f.endswith(".py"):
                continue
            if f in EXCLUDE_FILES:
                continue
            out.append(os.path.join(cur, f))
    out.sort(key=lambda p: p.replace(os.sep, "/"))
    return out


def relpath_from_root(path: str, root_dir: str) -> str:
    rp = os.path.relpath(path, root_dir)
    return rp.replace(os.sep, "/")


def parse_imports(mod: ast.Module) -> List[str]:
    imports: List[str] = []
    for node in mod.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            modname = node.module or ""
            # represent "from x import a, b" as "x:a,b"
            names = ",".join(a.name for a in node.names)
            level = "." * (node.level or 0)
            imports.append(f"{level}{modname}:{names}".strip(":"))
    imports.sort()
    return imports


def parse_defs(mod: ast.Module, file_rel: str) -> List[DefInfo]:
    defs: List[DefInfo] = []
    for node in mod.body:
        if isinstance(node, ast.FunctionDef):
            defs.append(DefInfo(node.name, "function", file_rel, getattr(node, "lineno", 0) or 0))
        elif isinstance(node, ast.AsyncFunctionDef):
            defs.append(DefInfo(node.name, "function", file_rel, getattr(node, "lineno", 0) or 0))
        elif isinstance(node, ast.ClassDef):
            defs.append(DefInfo(node.name, "class", file_rel, getattr(node, "lineno", 0) or 0))
            # methods
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defs.append(DefInfo(f"{node.name}.{sub.name}", "method", file_rel, getattr(sub, "lineno", 0) or 0))
    defs.sort(key=lambda d: (d.file_rel, d.lineno, d.kind, d.name))
    return defs


def _call_name(expr: ast.AST) -> Optional[str]:
    """
    Return a best-effort name for a callable:
      foo() -> "foo"
      mod.foo() -> "foo"
      self.foo() -> "foo"
      a.b.c() -> "c"
    """
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None


def parse_callsites(mod: ast.Module, file_rel: str) -> Dict[str, List[Tuple[str, int]]]:
    """
    Return mapping: callee_name -> list of (file_rel, lineno) where called.
    """
    out: Dict[str, List[Tuple[str, int]]] = {}

    class V(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            name = _call_name(node.func)
            if name:
                out.setdefault(name, []).append((file_rel, getattr(node, "lineno", 0) or 0))
            self.generic_visit(node)

    V().visit(mod)

    # make deterministic
    for k in list(out.keys()):
        out[k].sort(key=lambda t: (t[0], t[1]))
    return out


def load_ast(path: str) -> ast.Module:
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read()
    return ast.parse(txt, filename=path)


def build_index(root_dir: str) -> Tuple[
    Dict[str, List[str]],                 # file_rel -> imports
    Dict[str, List[DefInfo]],             # file_rel -> defs
    Dict[str, DefInfo],                   # simple name -> DefInfo (top-level functions/classes only; last-wins)
    Dict[str, Set[str]],                  # callee -> set(caller_file_rel)
    Dict[str, List[Tuple[str, int]]],     # callee -> callsites (file_rel, lineno)
]:
    file_imports: Dict[str, List[str]] = {}
    file_defs: Dict[str, List[DefInfo]] = {}
    name_to_def: Dict[str, DefInfo] = {}
    callee_to_files: Dict[str, Set[str]] = {}
    callee_to_calls: Dict[str, List[Tuple[str, int]]] = {}

    for abs_path in iter_py_files(root_dir):
        file_rel = relpath_from_root(abs_path, root_dir)
        mod = load_ast(abs_path)

        imps = parse_imports(mod)
        defs = parse_defs(mod, file_rel)
        calls = parse_callsites(mod, file_rel)

        file_imports[file_rel] = imps
        file_defs[file_rel] = defs

        # Only map simple (unqualified) top-level function/class names for trace roots.
        for d in defs:
            if d.kind in ("function", "class") and "." not in d.name:
                name_to_def[d.name] = d

        for callee, callsites in calls.items():
            callee_to_files.setdefault(callee, set()).add(file_rel)
            callee_to_calls.setdefault(callee, []).extend(callsites)

    # determinize callsites
    for callee in list(callee_to_calls.keys()):
        callee_to_calls[callee].sort(key=lambda t: (t[0], t[1]))
    return file_imports, file_defs, name_to_def, callee_to_files, callee_to_calls


def write_code_map(root_dir: str, file_imports: Dict[str, List[str]], file_defs: Dict[str, List[DefInfo]]) -> None:
    files = sorted(file_imports.keys())

    lines: List[str] = []
    lines.append("# vop_interwoven — code map (authoritative)")
    lines.append("")
    lines.append("## Scope")
    lines.append("- Generated from the `vop_interwoven/` folder this script was run from.")
    lines.append("- Deterministic listing of per-file imports and definitions (functions/classes/methods).")
    lines.append("")
    lines.append("## Files")
    lines.append("")

    for f in files:
        lines.append(f"### `{f}`")
        imps = file_imports.get(f, [])
        defs = file_defs.get(f, [])
        if imps:
            lines.append("")
            lines.append("**Imports**")
            for imp in imps:
                lines.append(f"- `{imp}`")
        if defs:
            lines.append("")
            lines.append("**Definitions**")
            for d in defs:
                lines.append(f"- `{d.name}` ({d.kind}, L{d.lineno})")
        lines.append("")

    with open(os.path.join(root_dir, OUT_CODE_MAP), "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines).rstrip() + "\n")


def _format_def(d: DefInfo) -> str:
    return f"- {d.file_rel}\n  - `{d.name}` (L{d.lineno})"


def write_symbol_index(
    root_dir: str,
    name_to_def: Dict[str, DefInfo],
    callee_to_files: Dict[str, Set[str]],
    callee_to_calls: Dict[str, List[Tuple[str, int]]],
) -> None:
    # High-signal first, then everything else
    all_names = sorted(name_to_def.keys())
    high = [n for n in HIGH_SIGNAL if n in name_to_def]
    rest = [n for n in all_names if n not in set(high)]

    def callsites_line(sym: str) -> Optional[str]:
        files = sorted(callee_to_files.get(sym, []))
        if not files:
            return None
        return f"- `{sym}`: " + ", ".join(files)

    lines: List[str] = []
    lines.append("# vop_interwoven symbol index (defs + callsites)")
    lines.append("")
    lines.append("This index lists definitions and approximate callsites (by file) for navigation-first debugging.")
    lines.append("Line numbers are from AST parsing of the current source.")
    lines.append("")
    lines.append("## High-signal symbols")
    lines.append("")
    lines.append("**Definitions**")
    for sym in high:
        lines.append(_format_def(name_to_def[sym]))
    lines.append("")
    lines.append("**Callsites (approx)**")
    any_calls = False
    for sym in high:
        cs = callsites_line(sym)
        if cs:
            any_calls = True
            lines.append(cs)
    if not any_calls:
        lines.append("- (none found)")
    lines.append("")

    lines.append("## All top-level definitions")
    lines.append("")
    for sym in all_names:
        d = name_to_def[sym]
        lines.append(f"- `{sym}` — {d.file_rel} (L{d.lineno})")
    lines.append("")

    # Also include a “callsite details” section for high-signal (with line numbers)
    lines.append("## High-signal callsite details (approx)")
    lines.append("")
    for sym in high:
        calls = callee_to_calls.get(sym, [])
        if not calls:
            continue
        lines.append(f"### `{sym}`")
        for (frel, ln) in calls[:2000]:  # cap; deterministic
            lines.append(f"- {frel}:L{ln}")
        lines.append("")

    with open(os.path.join(root_dir, OUT_SYMBOL_INDEX), "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines).rstrip() + "\n")


def build_trace_tree(
    name_to_def: Dict[str, DefInfo],
    callee_to_files: Dict[str, Set[str]],
    roots: List[str],
    max_depth: int = 4,
) -> List[str]:
    """
    Extremely lightweight "trace": for each root symbol, list the set of files that call each callee,
    expanding by name only. This is approximate (no control-flow / no per-function call attribution).
    """
    out: List[str] = []
    out.append("# vop_interwoven trace map (approximate call tree)")
    out.append("")
    out.append("_Regenerated from the current source snapshot under this folder._")
    out.append("")
    out.append("Notes:")
    out.append("- This is a name-based approximation (AST call names), not a precise runtime call graph.")
    out.append("- It is still useful for stage ownership and narrowing which files to inspect next.")
    out.append("")

    for root in roots:
        if root not in name_to_def:
            continue
        d0 = name_to_def[root]
        out.append(f"## Trace: `{root}` ({d0.file_rel}:L{d0.lineno})")
        out.append("")

        seen: Set[str] = set()
        frontier: List[Tuple[str, int]] = [(root, 0)]
        seen.add(root)

        while frontier:
            sym, depth = frontier.pop(0)
            if depth >= max_depth:
                continue

            callers = sorted(callee_to_files.get(sym, []))
            # only expand further for “interesting” callees: those that are also defined top-level
            # (keeps output compact & stable)
            if sym != root:
                indent = "  " * depth
                out.append(f"{indent}- `{sym}`")
                if callers:
                    out.append(f"{indent}  - called from: " + ", ".join(callers))

            # expand to next: for each file that calls sym, we don't know which callees it calls here;
            # so we only expand based on "high-signal" and known defs appearing as callees in those files.
            # Practical heuristic: expand only HIGH_SIGNAL and defs whose name appears as a callee.
            next_candidates: List[str] = []
            for cand in HIGH_SIGNAL:
                if cand in name_to_def and cand not in seen and cand in callee_to_files:
                    next_candidates.append(cand)

            # Also expand immediate “neighbors”: any callee that is defined and is called from somewhere
            # (kept conservative by requiring it to be in callee_to_files).
            for cand in name_to_def.keys():
                if cand in seen:
                    continue
                if cand not in callee_to_files:
                    continue
                # simple heuristic: only expand a limited set at each depth to avoid blow-up
                if cand.startswith("_"):
                    continue
                next_candidates.append(cand)

            # determinize and cap
            next_candidates = sorted(set(next_candidates))[:40]
            for cand in next_candidates:
                seen.add(cand)
                frontier.append((cand, depth + 1))

        out.append("")

    return out


def write_trace_map(
    root_dir: str,
    name_to_def: Dict[str, DefInfo],
    callee_to_files: Dict[str, Set[str]],
) -> None:
    roots = [r for r in HIGH_SIGNAL if r in name_to_def]
    if not roots:
        # fallback: pick a few canonical entrypoints if present
        for r in ("main", "__main__", "run", "cli"):
            if r in name_to_def:
                roots.append(r)

    lines = build_trace_tree(name_to_def, callee_to_files, roots=roots, max_depth=4)
    with open(os.path.join(root_dir, OUT_TRACE_MAP), "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines).rstrip() + "\n")


def main(argv: List[str]) -> int:
    # Run from current directory; treat it as the vop_interwoven root.
    root_dir = os.getcwd()

    # Guardrail: require that it "looks like" the package root.
    # (This avoids accidentally generating maps at repo root.)
    expected_any = {"pipeline.py", "entry_dynamo.py", "core", "revit"}
    present = set(os.listdir(root_dir))
    if not (expected_any & present):
        sys.stderr.write(
            "ERROR: This does not look like the vop_interwoven folder root.\n"
            "Run this script from the folder that contains pipeline.py / entry_dynamo.py / core/ / revit/.\n"
        )
        return 2

    file_imports, file_defs, name_to_def, callee_to_files, callee_to_calls = build_index(root_dir)

    write_code_map(root_dir, file_imports, file_defs)
    write_symbol_index(root_dir, name_to_def, callee_to_files, callee_to_calls)
    write_trace_map(root_dir, name_to_def, callee_to_files)

    print(f"Wrote: {OUT_CODE_MAP}")
    print(f"Wrote: {OUT_TRACE_MAP}")
    print(f"Wrote: {OUT_SYMBOL_INDEX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
