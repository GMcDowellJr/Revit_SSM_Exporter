#!/usr/bin/env python
"""Prove the Stage A capture path calls NO Revit geometry API.

Stage A captures a primitive: frame, pixels, colour-to-element map, bbox. Every
element fact it records comes from an AABB (``get_BoundingBox``) or from a
parameter read. It must never tessellate, triangulate or walk geometry -- that
is the legacy geometry path's job, and that path is still in the tree, one
import away.

Nothing enforced that. ``LinkedElementProxy`` even exposes ``get_Geometry()``,
and the Stage A path already handles those proxies, so the call is one
attribute away from compiling and running. An invariant stated only in prose is
an unasserted proof obligation (CLAUDE.md), so this asserts it.

WHAT IT PROVES
    No function reachable from the Stage A roots contains a geometry API call.

HOW IT CAN BE WRONG, AND WHAT IT DOES ABOUT IT
    Reachability is computed over a static call graph, so it must not quietly
    under-approximate. Three defences:

    1. Import aliases are RESOLVED. The first version of this walk did not do
       that, and ``from ... import expand_host_link_import_model_elements as
       _expand_elements`` silently cut the edge -- the answer was still "clean",
       but by luck, not by method. An alias that cannot be resolved is a
       refusal, not a skipped edge.
    2. A call through a computed callee (``getattr(obj, name)()``) cannot be
       resolved at all. Those are REFUSED (exit 2) with the site named, rather
       than assumed harmless.
    3. Name resolution is deliberately OVER-approximate: a bare name binds to
       every function of that name in the tree. That adds edges, never removes
       them, so it can only make the check stricter.

    A file that cannot be parsed, a root that does not exist, and a scan that
    matched zero files are all refusals. A validator earns its name by
    declining what it cannot decide, not by accumulating checks until no attack
    comes to mind.

EXIT CODES
    0  clean -- no geometry reachable from any root
    1  a geometry call IS reachable (the sites are printed)
    2  the question could not be decided (parse failure, missing root,
       empty scan, unresolvable callee)

Proven against a known-positive: tests/test_stage_a_no_geometry.py injects a
``get_Geometry`` call into the Stage A path and asserts exit 1, with a control
asserting the unmutated tree exits 0.
"""
from __future__ import print_function

import argparse
import ast
import collections
import os
import sys


# Revit geometry APIs. An ATTRIBUTE here is a geometry call on any object; a
# NAME here is a geometry type constructed directly.
GEOMETRY_ATTRS = frozenset([
    "get_Geometry", "Tessellate", "Triangulate", "GetCurves", "get_Curves",
    "GetGeometryObjectFromReference", "ComputeReferences",
    "IncludeNonVisibleObjects",
])
GEOMETRY_NAMES = frozenset([
    "Options", "GeometryInstance", "GeometryElement", "Solid", "Face", "Edge",
    "Mesh",
])

# The functions a Stage A run enters. init_view_raster and collect_view_elements
# run BEFORE pipeline.py's Stage A branch, so "Stage A only calls bbox and
# parameters" is a claim about them too, not just about the export itself.
DEFAULT_ROOTS = (
    "export_color_id_buffer_view",
    "collect_view_elements",
    "init_view_raster",
)


class Refusal(Exception):
    """The check cannot decide the question and must not report success."""


def _iter_py_files(roots):
    for root in roots:
        if not os.path.isdir(root):
            raise Refusal("scan root does not exist: {0}".format(root))
        for dirpath, _dirs, files in os.walk(root):
            if "__pycache__" in dirpath:
                continue
            for name in sorted(files):
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


def _alias_map(tree):
    """{local name: original name} for `from x import a as b` in one module."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for al in node.names:
                if al.asname:
                    aliases[al.asname] = al.name
    return aliases


def _scan(paths):
    functions = collections.defaultdict(set)   # name -> {(module, name)}
    callees = {}                               # (module, name) -> {callee names}
    geometry = {}                              # (module, name) -> {tokens}
    undecidable = []                           # (module, name, lineno)
    files = 0

    for path in paths:
        files += 1
        try:
            with open(path) as handle:
                tree = ast.parse(handle.read(), filename=path)
        except SyntaxError as exc:
            raise Refusal(
                "cannot parse {0}: {1}. A file absent from the walk is a file "
                "this check did not read, not a file that is clean. This repo "
                "targets IronPython 2 and CPython 3 both, so a syntax the "
                "running interpreter rejects is live, not hypothetical."
                .format(path, exc))

        aliases = _alias_map(tree)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            key = (path, node.name)
            functions[node.name].add(key)
            called, found = set(), set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and sub.attr in GEOMETRY_ATTRS:
                    found.add(sub.attr)
                if not isinstance(sub, ast.Call):
                    continue
                func = sub.func
                if isinstance(func, ast.Attribute):
                    called.add(func.attr)
                elif isinstance(func, ast.Name):
                    called.add(aliases.get(func.id, func.id))
                    if func.id in GEOMETRY_NAMES:
                        found.add(func.id + "()")
                elif isinstance(func, ast.Subscript):
                    # A .NET generic instantiated and called: SCG.List[ElementId]().
                    # Structurally a TYPE construction, not a dispatch on an
                    # element, so it is decidable rather than undecidable --
                    # decide it on the generic's own name.
                    base = func.value
                    base_name = (base.attr if isinstance(base, ast.Attribute)
                                 else base.id if isinstance(base, ast.Name) else None)
                    if base_name is None:
                        undecidable.append((path, node.name, getattr(sub, "lineno", -1)))
                    else:
                        called.add(aliases.get(base_name, base_name))
                        if base_name in GEOMETRY_NAMES:
                            found.add(base_name + "[]()")
                else:
                    # A computed callee -- getattr(obj, name)() and friends.
                    # Not resolvable, so not assumed safe.
                    undecidable.append((path, node.name, getattr(sub, "lineno", -1)))
            callees[key] = called
            if found:
                geometry[key] = found

    if files == 0:
        raise Refusal(
            "scanned 0 files. An empty scan certifies nothing; a typo in a "
            "path would otherwise report a clean result over no code at all.")
    return files, functions, callees, geometry, undecidable


def _reachable(root_names, functions, callees):
    seen, queue = set(), collections.deque()
    for name in root_names:
        keys = functions.get(name)
        if not keys:
            raise Refusal(
                "root function not found: {0}. A root that does not resolve "
                "makes every claim about what it reaches vacuous.".format(name))
        queue.extend(keys)
    while queue:
        key = queue.popleft()
        if key in seen:
            continue
        seen.add(key)
        for callee in callees.get(key, ()):
            for target in functions.get(callee, ()):
                if target not in seen:
                    queue.append(target)
    return seen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", default=["vop_interwoven"],
                        help="directories to scan (default: vop_interwoven)")
    parser.add_argument("--root", action="append", dest="roots",
                        help="Stage A entry function (repeatable)")
    args = parser.parse_args(argv)
    roots = tuple(args.roots) if args.roots else DEFAULT_ROOTS
    paths = args.paths or ["vop_interwoven"]

    try:
        files, functions, callees, geometry, undecidable = _scan(_iter_py_files(paths))
        reached = _reachable(roots, functions, callees)
        blocked = [u for u in undecidable if (u[0], u[1]) in reached]
        if blocked:
            raise Refusal(
                "{0} call site(s) inside the Stage A path use a computed callee, "
                "which this walk cannot resolve:\n{1}".format(
                    len(blocked),
                    "\n".join("    {0}::{1} line {2}".format(*b) for b in blocked)))
    except Refusal as exc:
        print("REFUSED: {0}".format(exc))
        print("\nSTAGE A GEOMETRY-FREE: NOT PROVEN")
        return 2

    print("scanned {0} .py file(s) under: {1}".format(files, ", ".join(paths)))
    print("roots: {0}".format(", ".join(roots)))
    print("functions reachable from the Stage A roots: {0}".format(len(reached)))

    hits = sorted((key, sorted(toks)) for key, toks in geometry.items() if key in reached)
    if hits:
        print("\n=== geometry APIs reachable from the Stage A path ===")
        for (module, name), tokens in hits:
            print("  {0}::{1}  -> {2}".format(module, name, ", ".join(tokens)))
        print("\nStage A records AABBs and parameters only. Geometry belongs to "
              "the legacy path.")
        print("\nSTAGE A GEOMETRY-FREE: VIOLATED ({0} function(s))".format(len(hits)))
        return 1

    total_geom = len(geometry)
    print("geometry-using functions in the tree: {0} (none reachable)".format(total_geom))
    if total_geom == 0:
        print("\nREFUSED: the tree contains no geometry calls at all, so a clean "
              "result here proves nothing about reachability.")
        print("\nSTAGE A GEOMETRY-FREE: NOT PROVEN")
        return 2
    print("\nSTAGE A GEOMETRY-FREE: PROVEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
