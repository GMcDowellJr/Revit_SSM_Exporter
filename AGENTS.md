# AGENTS.md — Revit SSM Exporter

Read this file at session start. It applies to OpenAI Codex, ChatGPT with
code interpreter, and any agent that reads AGENTS.md before working on the repo.

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

**After modifying code**, run `graphify update .` to keep the graph current
(AST-only re-extraction, no LLM cost).

## graphify skill (Codex / tool-use agents)

When the user types `/graphify`, invoke the skill at `.agents/skills/graphify/SKILL.md`.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates — not a reason to skip graphify.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.

## Recurring defect classes — read before fixing

Three classes account for every defect review has caught in this repo:
a quantity computed in two places and never composed; an identity claimed in a
comment and never asserted; test infrastructure that nothing tests.

`CLAUDE.md` ("Recurring Defect Classes") has the rules, the verification
discipline, and the `semgrep` / `vulture` install lines. The short version:

- a test that reimplements what it checks proves nothing — bind to production,
  extracting it if it is not callable;
- mutate production and confirm the suite goes red, or the test is not wired to
  the defect;
- watch the test COUNT, not just the colour;
- a lint rule unfalsified against a known-positive commit is a hope, not a rule.

## Core architecture constraints

See `CLAUDE.md` for the full list. The non-negotiable ones:

- 3D model geometry is the **only** occlusion truth — 2D annotations never occlude
- UV Classification (`TINY` / `LINEAR` / `AREAL`) determines occlusion authority
- Bare `except:` is **forbidden** — all errors must be recorded in `Diagnostics`
- Single source of truth for category inclusion and view bounds resolution
- Do not mix refactoring + behavior changes in the same commit
