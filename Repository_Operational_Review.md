# HISTORICAL OPERATIONAL REVIEW / SUPERSEDED

This operational review reflects a prior snapshot assessment.

**Superseded by `CURRENT_ARCHITECTURE.md` for current architecture truth and `ROADMAP.md` for prioritized remaining work.**

Use this document as historical context only.

---

# Repository Operational Review

## Executive Assessment
- **Active codebase is clearly identified** as `vop_interwoven/`, with legacy and archival directories kept for reference. Evidence: root `README.md` explicitly separates `legacy`, `archive/refactor1`, and `vop_interwoven` as the revised architecture.【F:README.md†L1-L3】
- **Architecture and principles are strongly documented**, including strict occlusion semantics and classification rules. Evidence: `CLAUDE.md` and `vop_interwoven/README.md` both codify the “3D model geometry is the ONLY occlusion truth” and “2D annotation NEVER occludes model geometry.”【F:CLAUDE.md†L35-L46】【F:vop_interwoven/README.md†L12-L19】
- **Implementation is claimed “feature complete,”** including CSV/PNG export, external sources, and diagnostics. Evidence: `CLAUDE.md` “Project Status” and `IMPLEMENTATION_PLAN.md` summary state full phase completion and SSM parity.【F:CLAUDE.md†L171-L196】【F:vop_interwoven/IMPLEMENTATION_PLAN.md†L1423-L1468】
- **There is visible technical debt around diagnostics and edge rasterization**, with explicit TODOs and a non-implemented edge mode. Evidence: `streaming.py` TODO for view signature; `pipeline.py` note that proxy edge rasterization is not implemented.【F:vop_interwoven/streaming.py†L29-L49】【F:vop_interwoven/pipeline.py†L2968-L2984】
- **Operational process exists but is partial**, with GitHub Actions for map generation and bare-except checks (though one workflow file appears suffixed `_dep`). Evidence: `.github/workflows/update-maps-on-pr.yml` and `no-bare-except.yml_dep`.【F:.github/workflows/update-maps-on-pr.yml†L1-L49】【F:.github/workflows/no-bare-except.yml_dep†L1-L22】
- **Test scaffolding is substantial**, with numerous pytest files and golden baselines; tests are emphasized in docs. Evidence: `tests/` directory listing plus test guidance in `CLAUDE.md` and `vop_interwoven/README.md`.【F:CLAUDE.md†L108-L139】【F:vop_interwoven/README.md†L172-L195】
- **Delivery appears to be copy/deploy rather than packaging**, which affects reproducibility and distribution. Evidence: `CLAUDE.md` notes “No setup.py” and deployment via copying to Revit/Dynamo environment.【F:CLAUDE.md†L156-L160】

## Observed Architecture
- **Primary system**: VOP Interwoven pipeline with config, pipeline, export, and Revit integration layers, plus core rasterization/geometry components. Evidence: directory and file breakdown in `CLAUDE.md` and `vop_interwoven/README.md`.【F:CLAUDE.md†L16-L67】【F:vop_interwoven/README.md†L18-L49】
- **Separation of concerns** is explicit: `core/` for algorithms (raster, geometry, silhouette, caches), `revit/` for API integration (view basis, collection, links), and `export/` for CSV writing. Evidence: architecture listings in `CLAUDE.md` and `vop_interwoven/README.md`.【F:CLAUDE.md†L26-L67】【F:vop_interwoven/README.md†L20-L49】
- **Diagnostics and strategy tracking** are first-class modules (`core/diagnostics.py`, `diagnostics/strategy_tracker.py`), implying observability is meant to be part of the runtime. Evidence: module listings in `CLAUDE.md` and `vop_interwoven/README.md`.【F:CLAUDE.md†L38-L63】【F:vop_interwoven/README.md†L30-L47】
- **Testing layout**: root-level `tests/` includes unit tests, golden baselines, and Dynamo integration tests. Evidence: explicit doc note plus directory contents. 【F:CLAUDE.md†L68-L88】【F:vop_interwoven/README.md†L50-L59】

## Signals of Intent
- **Explicit non-negotiable architectural principles** (occlusion semantics, proxy behavior, UV classification authority). Evidence: `CLAUDE.md` “Core Architecture Principles” and `vop_interwoven/README.md` “Core Principles.”【F:CLAUDE.md†L35-L55】【F:vop_interwoven/README.md†L12-L19】
- **Strict refactor discipline and error-handling expectations** (no bare except, diagnostics required, single source of truth). Evidence: `vop_interwoven/docs/refactor_rules.md`.【F:vop_interwoven/docs/refactor_rules.md†L1-L32】
- **Planned and/or completed milestones** are documented by phase, with specific “future enhancements” called out (RLE compression, multi-view parallelization, adaptive thresholds, etc.). Evidence: `vop_interwoven/README.md` “Future Enhancements” and `IMPLEMENTATION_PLAN.md` summary/future list.【F:vop_interwoven/README.md†L231-L236】【F:vop_interwoven/IMPLEMENTATION_PLAN.md†L1469-L1490】
- **Automated maintenance intent** for navigation maps and linting. Evidence: `update-maps-on-pr` workflow and bare-except check workflow. 【F:.github/workflows/update-maps-on-pr.yml†L1-L49】【F:.github/workflows/no-bare-except.yml_dep†L1-L22】
- **Process emphasis on tests and golden baselines**, suggesting regression protection as a goal. Evidence: `CLAUDE.md` testing sections and tools list. 【F:CLAUDE.md†L108-L139】

## Implementation vs Intent
### Aligned
- **Core pipeline capabilities appear implemented and documented**, including collection, rasterization, annotation handling, exports, and diagnostics. Evidence: “feature complete” statements in `CLAUDE.md` and phase completion summary in `IMPLEMENTATION_PLAN.md`.【F:CLAUDE.md†L171-L196】【F:vop_interwoven/IMPLEMENTATION_PLAN.md†L1423-L1468】
- **Tests are present and used as part of workflow guidance**, matching the stated emphasis on verification. Evidence: test instructions in `CLAUDE.md`/`vop_interwoven/README.md` and the populated `tests/` directory. 【F:CLAUDE.md†L108-L139】【F:vop_interwoven/README.md†L172-L195】
- **Process rules (no bare except, diagnostics required) are codified** and supported by tooling. Evidence: `refactor_rules.md` and `no-bare-except.yml_dep`.【F:vop_interwoven/docs/refactor_rules.md†L1-L32】【F:.github/workflows/no-bare-except.yml_dep†L1-L22】

### Divergent
- **Diagnostics gap is acknowledged in code**, suggesting not all errors are yet wired into `Diagnostics` as intended. Evidence: repeated TODOs like “Add diagnostics when diag becomes available” (example from `streaming.py`).【F:vop_interwoven/streaming.py†L29-L49】
- **Proxy edge rasterization is explicitly not implemented** even though “edges” mode exists. Evidence: `pipeline.py` states edge rasterization is not currently implemented and uses `pass`.【F:vop_interwoven/pipeline.py†L2968-L2984】
- **Workflow automation appears partially configured**, with the bare-except workflow filename suffixed `_dep`, which could indicate a disabled or deprecated workflow (not enough evidence to confirm runtime behavior). Evidence: `.github/workflows/no-bare-except.yml_dep`.【F:.github/workflows/no-bare-except.yml_dep†L1-L22】

### Indeterminate
- **Production usage posture** (e.g., release cadence, support SLAs) is not clearly defined in repo artifacts. *Not enough evidence.*
- **Runtime deployment environment stability** is unclear beyond copy/deploy guidance. *Not enough evidence.*

## Workflow & Process Inference
- **Testing-first orientation**: documentation calls for pytest, Dynamo tests, and golden baselines, suggesting a validation-driven workflow. Evidence: `CLAUDE.md` testing and golden baseline instructions.【F:CLAUDE.md†L108-L139】
- **CI automation for navigation map updates** indicates a maintenance process tied to PRs. Evidence: `update-maps-on-pr.yml` workflow. 【F:.github/workflows/update-maps-on-pr.yml†L1-L49】
- **Quality gate intent**: bare-except checks are emphasized, aligning with “no silent failure” rules. Evidence: `refactor_rules.md` and the bare-except workflow definition. 【F:vop_interwoven/docs/refactor_rules.md†L1-L32】【F:.github/workflows/no-bare-except.yml_dep†L1-L22】

## Technical Debt Indicators
- **Explicit TODOs and placeholder passes** in streaming and pipeline code indicate deferred work (diagnostics, edge rasterization). Evidence: TODO in `streaming.py` and `pass` in `_stamp_proxy_edges`.【F:vop_interwoven/streaming.py†L29-L49】【F:vop_interwoven/pipeline.py†L2968-L2984】
- **Future enhancements list** suggests pending improvements (RLE compression, multi-view parallelization, adaptive thresholds). Evidence: `vop_interwoven/README.md` future enhancements section.【F:vop_interwoven/README.md†L231-L236】
- **Legacy and archive folders** imply historical refactors and code divergence that may require ongoing context. Evidence: root `README.md` directory description.【F:README.md†L1-L3】

## Operational Risks (ranked)
1. **Diagnostics completeness risk**: code-level TODOs imply gaps in the diagnostics contract, potentially reducing observability in failure cases. Evidence: TODO in `streaming.py` and related diagnostic placeholders in pipeline. 【F:vop_interwoven/streaming.py†L29-L49】【F:vop_interwoven/pipeline.py†L2968-L2984】
2. **Feature-mode mismatch risk**: “edges” proxy mode exists but is not implemented, which can lead to unexpected runtime behavior if enabled. Evidence: `_stamp_proxy_edges` note and `pass`.【F:vop_interwoven/pipeline.py†L2968-L2984】
3. **Workflow automation ambiguity**: bare-except workflow appears potentially disabled by filename suffix, weakening enforcement of a stated rule. Evidence: `.github/workflows/no-bare-except.yml_dep`.【F:.github/workflows/no-bare-except.yml_dep†L1-L22】
4. **Deployment reproducibility risk**: no packaging/build system is described; deployment relies on copying files into Revit/Dynamo environments, which can be fragile. Evidence: `CLAUDE.md` environment notes (“No setup.py”).【F:CLAUDE.md†L156-L160】
5. **Knowledge concentration risk**: extensive guidance is embedded in assistant-oriented docs (CLAUDE.md) and large implementation plan, suggesting operational knowledge is document-heavy rather than tool-enforced. Evidence: `CLAUDE.md` and `IMPLEMENTATION_PLAN.md` breadth. 【F:CLAUDE.md†L1-L213】【F:vop_interwoven/IMPLEMENTATION_PLAN.md†L1-L1490】

## Highest-Leverage Next Moves
1. **Complete or explicitly gate the “edges” proxy mode** to avoid accidental usage of a non-implemented path. Evidence: `_stamp_proxy_edges` note and `pass`.【F:vop_interwoven/pipeline.py†L2968-L2984】
2. **Close the diagnostics TODO loop** by wiring available diagnostics where TODOs are present, aligning with the stated “no silent failure” rules. Evidence: diagnostics rule set and TODO markers. 【F:vop_interwoven/docs/refactor_rules.md†L1-L18】【F:vop_interwoven/streaming.py†L29-L49】
3. **Clarify bare-except workflow status** (rename/deprecate or re-enable) so enforcement matches stated refactor rules. Evidence: `no-bare-except.yml_dep` and refactor rules. 【F:.github/workflows/no-bare-except.yml_dep†L1-L22】【F:vop_interwoven/docs/refactor_rules.md†L1-L18】
4. **Document deployment and runtime packaging expectations** in a user-facing README section to reduce copy/deploy ambiguity. Evidence: `CLAUDE.md` notes “No setup.py.”【F:CLAUDE.md†L156-L160】
5. **Prioritize one of the declared future enhancements** (e.g., adaptive thresholds or RLE compression) if it remains strategically important, since these are explicitly called out. Evidence: future enhancements list. 【F:vop_interwoven/README.md†L231-L236】
