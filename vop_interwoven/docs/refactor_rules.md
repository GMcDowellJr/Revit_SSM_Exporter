# Refactor Rules (VOP Interwoven)

These rules apply to all changes touching:
- pipeline execution
- element collection
- view bounds
- rasterization / occlusion
- exporters

## 1. No Silent Failure
- Bare `except:` is forbidden.
- All recoverable errors MUST be:
  - recorded in `Diagnostics`
  - categorized (collection, bounds, geometry, export)
- If recovery is not clearly safe, fail loudly.

## 2. Explicit Semantics
Any concept that can be interpreted multiple ways must be explicit:
- “model present” must specify:
  - triangles / depth truth
  - proxy presence
  - edge presence
- View support must be capability-based, not type-based.

## 3. Single Source of Truth
- Category inclusion/exclusion is defined in one place only.
- View bounds resolution is centralized.
- Source identity is normalized (`HOST | LINK | DWG`) before rasterization.

## 4. Worst-Case First
Code must behave correctly under:
- null or missing geometry
- unloaded or partially broken links
- rotated transforms
- extreme view scales
- large models

## 5. Small, Reviewable Changes
- Refactors must be split into focused commits.
- Do not mix:
  - refactoring + behavior change
  - performance + semantics
  - cleanup + logic
