# GitHub Copilot Instructions — Revit SSM Exporter

## graphify — Query the knowledge graph before browsing source

This project has a knowledge graph at `graphify-out/` with god nodes, community
structure, and cross-file relationships covering all 120+ source files.

**When `graphify-out/graph.json` exists, use the graph before reading source files:**

```bash
# Broad question about how something works
graphify query "<question>"

# Relationship between two modules or concepts
graphify path "<A>" "<B>"

# Focused explanation of a single concept
graphify explain "<concept>"
```

These commands return a scoped subgraph — much smaller and faster than grepping
raw files or reading code one file at a time.

**When to read raw source instead:**
- Modifying specific code (you need the exact text to edit)
- Debugging a precise runtime value or stack trace
- The graph query returns no useful results

**Broad architecture reference:**
Read `graphify-out/GRAPH_REPORT.md` for god nodes, community layout, and
surprising cross-file connections. Do not read it for every question — use
`graphify query` first.

**After modifying code**, run `graphify update .` to keep the graph current
(AST-only re-extraction, no LLM cost).

## Core architecture constraints

See `CLAUDE.md` for the full list. The non-negotiable ones:

- 3D model geometry is the **only** occlusion truth — 2D annotations never occlude
- UV Classification (`TINY` / `LINEAR` / `AREAL`) determines occlusion authority
- Bare `except:` is **forbidden** — all errors must be recorded in `Diagnostics`
- Single source of truth for category inclusion and view bounds resolution
- Do not mix refactoring + behavior changes in the same commit
