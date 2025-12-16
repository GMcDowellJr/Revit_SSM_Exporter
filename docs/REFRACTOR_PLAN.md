# Refactor Plan (v4)

Source of truth: modularization plan + acceptance criteria.
- Main is glue only (keep entrypoint thin).
- Single source of truth: ViewResult returned by process_view().
- Enforce debug size caps.

Phases:
0) Golden baseline harness
1) Extract types/export_csv/debug/config
2) Extract view-basis/transforms
3) Extract grid + counts
4) Extract projection + silhouette
5) Extract Revit collection/link policy
