"""Deterministic, Revit-free staged campaign planner.

This module deliberately depends only on the Python standard library.  It consumes
the executor and analyzer JSON contracts; it never imports or dispatches probes.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "1.0"
RASTER_ARTIFACT_SUFFIXES = (".png", ".tif", ".tiff", ".jpg", ".jpeg", ".bmp")
STATES = ("PLANNED", "ELIGIBLE", "BATCHED", "EXECUTED", "ANALYZED", "PASSED",
          "FAILED", "INCONCLUSIVE", "BLOCKED", "NEEDS_DIAGNOSTIC", "SKIPPED",
          "SUPERSEDED")
TERMINAL = {"PASSED", "FAILED", "INCONCLUSIVE", "SKIPPED", "SUPERSEDED"}
LEGAL_TRANSITIONS = {
    "PLANNED": {"ELIGIBLE", "BLOCKED", "SKIPPED", "SUPERSEDED", "NEEDS_DIAGNOSTIC"},
    "ELIGIBLE": {"BATCHED", "BLOCKED", "SKIPPED", "SUPERSEDED"},
    "BATCHED": {"EXECUTED", "FAILED", "ELIGIBLE", "SUPERSEDED"},
    "EXECUTED": {"ANALYZED", "FAILED", "INCONCLUSIVE", "SUPERSEDED"},
    "ANALYZED": {"PASSED", "FAILED", "INCONCLUSIVE", "SUPERSEDED"},
    "FAILED": {"NEEDS_DIAGNOSTIC", "SUPERSEDED"},
    # PASSED is reachable only via an authorized manual review that resolves a
    # gate-scoped MANUAL_SEMANTIC_REVIEW_REQUIRED outcome (see
    # record_manual_review); it is never a side effect of an unrelated review.
    "INCONCLUSIVE": {"NEEDS_DIAGNOSTIC", "SUPERSEDED", "PASSED", "FAILED"},
    "BLOCKED": {"ELIGIBLE", "SKIPPED", "SUPERSEDED", "NEEDS_DIAGNOSTIC"},
    "NEEDS_DIAGNOSTIC": {"ELIGIBLE", "BLOCKED", "SUPERSEDED"},
    "PASSED": {"SUPERSEDED"}, "SKIPPED": {"SUPERSEDED"}, "SUPERSEDED": set(),
}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CampaignError(ValueError):
    """Invalid campaign, state, or evidence."""


class ConfigurationDriftError(CampaignError):
    """Raised only by _assert_binding on CONFIGURATION_DRIFT.

    By the time this is raised, `state` has already been fully and
    self-containedly mutated (every job superseded, next_recommendation set,
    a history event recorded) - never a partial application of unrelated
    work. This is the *only* CampaignError subtype whose in-memory mutation
    the CLI persists to disk after an error; any other CampaignError (a
    malformed manifest, an invalid analysis record, an unauthorized
    diagnostic request, ...) must leave the on-disk state file exactly as it
    was before the command ran.
    """


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_fingerprint(value: Any) -> str:
    """Hash canonical JSON, excluding descriptive notes and volatile timestamps."""
    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            # analyzed_at is stripped so re-running the analyzer on an
            # unchanged manifest and re-ingesting it is a clean idempotent
            # no-op (same record_id, same digest) rather than a spurious
            # ANALYSIS_CONFLICT caused only by a fresh timestamp.
            return {k: clean(v) for k, v in sorted(item.items())
                    if k not in {"notes", "created_at", "updated_at", "analyzed_at"}}
        if isinstance(item, list):
            return [clean(v) for v in item]
        return item
    encoded = json.dumps(clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stable_job_id(campaign_id: str, stage: str, view_key: str, probe: str,
                  case: str, variant: str, repetition: int) -> str:
    """Return a readable stable identifier plus a collision-resistant identity suffix."""
    parts = [campaign_id, stage, view_key, probe, case, variant, str(repetition)]
    slug = ".".join(re.sub(r"[^A-Za-z0-9._-]+", "-", p).strip("-.") or "none"
                    for p in parts)
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:12]
    return (slug[:114].rstrip(".-") + "." + digest)[:127]


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CampaignError(f"{path} must contain a JSON object")
    return value


def atomic_write(path: str | Path, value: dict[str, Any]) -> None:
    """Replace only after a complete, flushed write; the old state survives failures."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        try: os.unlink(temporary)
        except OSError: pass
        raise


def validate_campaign(campaign: dict[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "campaign_id", "description", "document_expectations",
                "view_registry", "job_templates", "stages", "dependencies",
                "acceptance_requirements", "execution_defaults", "diagnostic_expansion_rules"}
    missing = sorted(required - set(campaign))
    if missing: raise CampaignError(f"missing campaign fields: {missing}")
    if campaign["schema_version"] != SCHEMA_VERSION: raise CampaignError("unsupported campaign schema_version")
    if not ID_RE.match(str(campaign["campaign_id"])): raise CampaignError("invalid campaign_id")
    if not isinstance(campaign["view_registry"], dict) or not campaign["view_registry"]:
        raise CampaignError("view_registry must be a non-empty object")
    for key, view in campaign["view_registry"].items():
        needed = {"unique_id", "expected_name", "expected_view_type", "expected_crop_active", "roles", "source_content_tags"}
        if not needed <= set(view): raise CampaignError(f"view {key} is missing {sorted(needed-set(view))}")
        if view["expected_view_type"].lower() in {"drafting", "draftingview"} and "alignment_matrix" in view["roles"]:
            raise CampaignError(f"drafting view {key} cannot have alignment_matrix role")
    stage_ids, job_keys = [], set()
    for stage in campaign["stages"]:
        if not {"stage_id", "description", "jobs"} <= set(stage): raise CampaignError("each stage requires stage_id, description, jobs")
        stage_ids.append(stage["stage_id"])
        for job in stage["jobs"]:
            needed = {"job_key", "view_key", "probe_id", "case", "variant", "repetition", "settings"}
            if not needed <= set(job): raise CampaignError(f"job is missing {sorted(needed-set(job))}")
            if job["job_key"] in job_keys: raise CampaignError(f"duplicate job_key: {job['job_key']}")
            if job["view_key"] not in campaign["view_registry"]: raise CampaignError(f"unknown view_key: {job['view_key']}")
            job_keys.add(job["job_key"])
    if len(stage_ids) != len(set(stage_ids)): raise CampaignError("duplicate stage_id")
    closure_ids = set()
    for fallback in campaign.get("conditional_fallbacks", []):
        needed = {"fallback_id", "trigger_job", "trigger_reason_codes", "fallback_job"}
        if not needed <= set(fallback): raise CampaignError(f"conditional_fallback is missing {sorted(needed-set(fallback))}")
        if fallback["fallback_id"] in closure_ids: raise CampaignError(f"duplicate fallback_id: {fallback['fallback_id']}")
        closure_ids.add(fallback["fallback_id"])
        if fallback["trigger_job"] not in job_keys:
            raise CampaignError(f"conditional_fallback {fallback['fallback_id']} references unknown trigger_job")
        codes = fallback["trigger_reason_codes"]
        if not isinstance(codes, list) or not codes or not all(isinstance(c, str) for c in codes):
            raise CampaignError(f"conditional_fallback {fallback['fallback_id']} trigger_reason_codes must be a non-empty list of strings")
        fjob = fallback["fallback_job"]
        fneeded = {"job_key", "view_key", "probe_id", "case", "variant", "repetition", "settings"}
        if not fneeded <= set(fjob): raise CampaignError(f"conditional_fallback {fallback['fallback_id']} fallback_job is missing {sorted(fneeded-set(fjob))}")
        if fjob["job_key"] in job_keys: raise CampaignError(f"conditional_fallback {fallback['fallback_id']} fallback_job.job_key collides with an existing job_key")
        if fjob["view_key"] not in campaign["view_registry"]: raise CampaignError(f"conditional_fallback {fallback['fallback_id']} fallback_job references unknown view_key")
    for dep in campaign["dependencies"]:
        if dep.get("requires_closure") is not None:
            if dep.get("job") not in job_keys: raise CampaignError(f"dependency references unknown job: {dep}")
            if dep["requires_closure"] not in closure_ids: raise CampaignError(f"dependency references unknown requires_closure: {dep}")
        elif dep.get("job") not in job_keys or dep.get("requires_job") not in job_keys:
            raise CampaignError(f"dependency references unknown job: {dep}")
        if dep.get("statuses", ["PASS"]) and not set(dep.get("statuses", ["PASS"])) <= {"PASS", "FAIL", "INCONCLUSIVE", "BLOCKED"}:
            raise CampaignError("dependency statuses must be analyzer acceptance values")
    defaults = campaign["execution_defaults"]
    if not isinstance(defaults.get("batch_size"), int) or defaults["batch_size"] < 1:
        raise CampaignError("execution_defaults.batch_size must be positive")
    return campaign


def _expanded_jobs(campaign: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = []
    for stage_index, stage in enumerate(campaign["stages"]):
        for job_index, configured in enumerate(stage["jobs"]):
            job = copy.deepcopy(configured)
            job.update({"stage_id": stage["stage_id"], "stage_order": stage_index,
                        "job_order": job_index})
            job["job_id"] = stable_job_id(campaign["campaign_id"], stage["stage_id"],
                job["view_key"], job["probe_id"], job["case"], job["variant"], job["repetition"])
            job["configuration_fingerprint"] = canonical_fingerprint(job)
            jobs.append(job)
    return jobs


def _event(state: dict[str, Any], kind: str, **details: Any) -> None:
    event_id = canonical_fingerprint({"kind": kind, **details})
    if any(e["event_id"] == event_id for e in state["history"]): return
    state["history"].append({"event_id": event_id, "at": utc_now(), "kind": kind, **details})


def transition(state: dict[str, Any], job_id: str, target: str, reason_code: str,
               details: Any = None) -> bool:
    job = state["jobs"][job_id]; source = job["status"]
    if source == target: return False
    if target not in LEGAL_TRANSITIONS.get(source, set()):
        raise CampaignError(f"illegal job transition {source} -> {target} for {job_id}")
    job["status"] = target
    job["status_reason"] = {"code": reason_code, "details": details}
    _event(state, "JOB_TRANSITION", job_id=job_id, source=source, target=target,
           reason_code=reason_code, details=details)
    return True


def initialize_state(campaign: dict[str, Any], now: str | None = None) -> dict[str, Any]:
    validate_campaign(campaign); now = now or utc_now(); fingerprint = canonical_fingerprint(campaign)
    state: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "campaign_id": campaign["campaign_id"],
        "campaign_configuration_fingerprint": fingerprint, "created_at": now, "updated_at": now,
        "view_coverage": {}, "stage_status": {}, "jobs": {}, "ingested_revit_runs": {},
        "ingested_analysis_records": {}, "conflicts": [], "blocked_jobs": {},
        "diagnostic_requests": {}, "manual_review_requirements": {}, "closures": {},
        "next_recommendation": {}, "history": []}
    for job in _expanded_jobs(campaign):
        state["jobs"][job["job_id"]] = {**job, "status": "PLANNED",
            "status_reason": {"code": "INITIALIZED"}, "batch_ids": [], "run_ids": [],
            "analysis_record_ids": [], "artifact_references": [], "execution_fingerprints": {}}
        if job.get("manual_review_required"):
            # Evidence is not yet known at init time (the job has not run
            # yet); it is filled in from the job's own artifact_references
            # once analysis is ingested (see _sync_review_evidence). A
            # campaign-authored requirement therefore starts PENDING with no
            # artifact_references, exactly as before, but can never be
            # accepted/rejected (record_manual_review) nor let the campaign
            # report COMPLETE (compute_next_recommendation) unless a
            # reviewable artifact actually arrives for this job.
            state["manual_review_requirements"][job["job_id"]] = {
                "status": "PENDING", "reason": job.get("manual_review_reason", "CONFIGURED"),
                "artifact_references": [],
            }
    _event(state, "STATE_INITIALIZED", campaign_fingerprint=fingerprint)
    evaluate(campaign, state)
    return state


def _assert_binding(campaign: dict[str, Any], state: dict[str, Any]) -> None:
    if state.get("campaign_id") != campaign.get("campaign_id"): raise CampaignError("campaign/state identity mismatch")
    current = canonical_fingerprint(campaign)
    if state.get("campaign_configuration_fingerprint") != current:
        for job in state.get("jobs", {}).values():
            if job["status"] != "SUPERSEDED": transition(state, job["job_id"], "SUPERSEDED", "CONFIGURATION_DRIFT")
        state["next_recommendation"] = {"code": "INVALID_CAMPAIGN_STATE", "reason": "CONFIGURATION_DRIFT", "current_fingerprint": current}
        raise ConfigurationDriftError("campaign configuration drift; prior jobs were superseded, initialize amended state")


def _dependencies(campaign: dict[str, Any], job: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for d in campaign["dependencies"] if d["job"] == job["job_key"]]


# Closures resolved this way (RESOLVED_PRIMARY / RESOLVED_FALLBACK_PASS) are
# treated as an upstream "PASS" for any dependency gated on them.
_CLOSURE_PASS = {"RESOLVED_PRIMARY", "RESOLVED_FALLBACK_PASS"}
_CLOSURE_FAIL = {"RESOLVED_FAILED", "RESOLVED_FALLBACK_FAILED"}
_CLOSURE_OPEN = {"PENDING", "AWAITING_FALLBACK_EXECUTION"}


def _closure_candidate_settled(state: dict[str, Any], job: dict[str, Any]) -> bool:
    """A job is settled for closure-resolution purposes once it is terminal
    AND, if INCONCLUSIVE, not still awaiting a PENDING gate-scoped manual
    review that could still convert it to PASSED/FAILED. Resolving the
    closure the instant a candidate goes INCONCLUSIVE - without waiting for
    that review - would prematurely lock in RESOLVED_*_FAILED even though an
    ACCEPTED review is about to make it PASSED."""
    if job["status"] != "INCONCLUSIVE":
        return job["status"] in TERMINAL
    requirement = state["manual_review_requirements"].get(job["job_id"])
    return not (requirement and requirement["status"] == "PENDING")


def _apply_conditional_fallbacks(campaign: dict[str, Any], state: dict[str, Any]) -> None:
    """Deterministically resolve Stage-1-style mutation closures.

    A closure watches one ``trigger_job``.  If it PASSES, the closure resolves
    immediately on the primary job.  If it fails/inconclusive *and* its actual
    reason codes are a superset of the configured ``trigger_reason_codes``
    (e.g. a template-control blockage, never an arbitrary FAIL/INCONCLUSIVE),
    a fallback job is materialized once and the closure awaits its outcome.
    Any other terminal outcome resolves the closure as failed without ever
    scheduling the fallback or converting the trigger's result into a pass.
    """
    key_map = {j["job_key"]: j for j in state["jobs"].values()}
    for fallback in campaign.get("conditional_fallbacks", []):
        closure_id = fallback["fallback_id"]
        closure = state["closures"].setdefault(closure_id, {
            "status": "PENDING", "trigger_job_key": fallback["trigger_job"],
            "primary_job_id": None, "fallback_job_id": None,
            "candidate_job_id": None, "resolved_at": None})
        if closure["status"] == "AWAITING_FALLBACK_EXECUTION":
            candidate = state["jobs"].get(closure["fallback_job_id"])
            if candidate and _closure_candidate_settled(state, candidate):
                resolved = "RESOLVED_FALLBACK_PASS" if candidate["status"] == "PASSED" else "RESOLVED_FALLBACK_FAILED"
                closure.update({"status": resolved, "candidate_job_id": candidate["job_id"], "resolved_at": utc_now()})
                _event(state, "CLOSURE_RESOLVED", closure_id=closure_id, status=resolved, candidate_job_id=candidate["job_id"])
            continue
        if closure["status"] != "PENDING": continue
        trigger = key_map.get(fallback["trigger_job"])
        if not trigger or not _closure_candidate_settled(state, trigger): continue
        closure["primary_job_id"] = trigger["job_id"]
        if trigger["status"] == "PASSED":
            closure.update({"status": "RESOLVED_PRIMARY", "candidate_job_id": trigger["job_id"], "resolved_at": utc_now()})
            _event(state, "CLOSURE_RESOLVED", closure_id=closure_id, status="RESOLVED_PRIMARY", candidate_job_id=trigger["job_id"])
            continue
        actual_codes = set(((trigger.get("status_reason") or {}).get("details") or {}).get("reason_codes") or [])
        trigger_codes = set(fallback["trigger_reason_codes"])
        if trigger["status"] in {"FAILED", "INCONCLUSIVE"} and trigger_codes <= actual_codes:
            spec = copy.deepcopy(fallback["fallback_job"])
            spec.update({"stage_id": trigger["stage_id"], "stage_order": trigger["stage_order"],
                        "job_order": trigger["job_order"] + 0.5})
            spec["job_id"] = stable_job_id(campaign["campaign_id"], spec["stage_id"], spec["view_key"],
                spec["probe_id"], spec["case"], spec["variant"], spec["repetition"])
            if spec["job_id"] not in state["jobs"]:
                spec["configuration_fingerprint"] = canonical_fingerprint(spec)
                spec.update({"status": "PLANNED",
                    "status_reason": {"code": "CLOSURE_FALLBACK_MATERIALIZED",
                                      "details": {"closure_id": closure_id, "primary_job_id": trigger["job_id"]}},
                    "batch_ids": [], "run_ids": [], "analysis_record_ids": [], "artifact_references": [],
                    "execution_fingerprints": {},
                    "provenance": {"closure_id": closure_id, "fallback_of_job_id": trigger["job_id"],
                                   "trigger_reason_codes_matched": sorted(trigger_codes)}})
                state["jobs"][spec["job_id"]] = spec
            closure.update({"status": "AWAITING_FALLBACK_EXECUTION", "fallback_job_id": spec["job_id"],
                            "candidate_job_id": spec["job_id"]})
            _event(state, "CLOSURE_FALLBACK_MATERIALIZED", closure_id=closure_id,
                   fallback_job_id=spec["job_id"], primary_job_id=trigger["job_id"])
        else:
            closure.update({"status": "RESOLVED_FAILED", "candidate_job_id": trigger["job_id"], "resolved_at": utc_now()})
            _event(state, "CLOSURE_RESOLVED", closure_id=closure_id, status="RESOLVED_FAILED", candidate_job_id=trigger["job_id"])


def evaluate(campaign: dict[str, Any], state: dict[str, Any]) -> None:
    """Recompute deterministic gates without regressing batched or completed work."""
    _apply_conditional_fallbacks(campaign, state)
    key_map = {j["job_key"]: j for j in state["jobs"].values()}
    for job in sorted(state["jobs"].values(), key=lambda j: (j["stage_order"], j["job_order"])):
        # NEEDS_DIAGNOSTIC is a holding state for the failed source job.  Only
        # the separately materialized diagnostic jobs may re-enter eligibility.
        if job["status"] not in {"PLANNED", "ELIGIBLE", "BLOCKED"}: continue
        deps = _dependencies(campaign, job)
        failures, waiting = [], []
        for dep in deps:
            wanted = set(dep.get("statuses", ["PASS"]))
            if dep.get("requires_closure") is not None:
                closure = state["closures"].get(dep["requires_closure"])
                if not closure or closure["status"] in _CLOSURE_OPEN:
                    waiting.append({"requires_closure": dep["requires_closure"],
                                    "status": closure["status"] if closure else "PENDING"})
                    continue
                actual = "PASS" if closure["status"] in _CLOSURE_PASS else "FAIL"
                if actual in wanted: continue
                failures.append({"requires_closure": dep["requires_closure"], "actual": actual,
                                 "wanted": sorted(wanted), "candidate_job_id": closure.get("candidate_job_id")})
                continue
            upstream = key_map[dep["requires_job"]]
            # BLOCKED is a legal, explicitly-configured "concluded" outcome (e.g.
            # "external-source investigation may proceed once the independent
            # alignment/mutation matrix has concluded, pass or fail or blocked").
            # It is never satisfied implicitly - only when a dependency row lists
            # it in `statuses` does a BLOCKED upstream count as satisfying.
            actual = {"PASSED": "PASS", "FAILED": "FAIL", "INCONCLUSIVE": "INCONCLUSIVE", "BLOCKED": "BLOCKED"}.get(upstream["status"])
            if actual in wanted: continue
            if actual in {"FAIL", "INCONCLUSIVE", "BLOCKED"} or upstream["status"] in {"NEEDS_DIAGNOSTIC", "SKIPPED", "SUPERSEDED"}:
                failures.append({"requires_job": upstream["job_id"], "actual": actual or upstream["status"], "wanted": sorted(wanted)})
            else: waiting.append({"requires_job": upstream["job_id"], "status": upstream["status"]})
        target, reason, detail = ("BLOCKED", "DEPENDENCY_FAILED", failures) if failures else (("PLANNED", "AWAITING_DEPENDENCY", waiting) if waiting else ("ELIGIBLE", "DEPENDENCIES_SATISFIED", []))
        if target != job["status"]:
            if target == "PLANNED" and job["status"] in {"BLOCKED", "NEEDS_DIAGNOSTIC"}: continue
            transition(state, job["job_id"], target, reason, detail)
        elif target == "PLANNED": job["status_reason"] = {"code": reason, "details": detail}
    _summarize(campaign, state)


_STAGE_SUCCESS = {"PASSED", "SKIPPED"}


def _stage_status(jobs: list[dict[str, Any]]) -> str:
    if not jobs: return "ACTIVE"
    statuses = {j["status"] for j in jobs}
    if statuses <= TERMINAL:
        # All jobs reached a terminal state, but "COMPLETE" must never read as
        # success unless every job actually succeeded (PASSED/SKIPPED). A
        # stage where a job FAILED/INCONCLUSIVE/SUPERSEDED is complete-but-not-
        # successful and must say so explicitly.
        return "COMPLETE" if statuses <= _STAGE_SUCCESS else "COMPLETE_WITH_FAILURES"
    if statuses <= TERMINAL | {"BLOCKED", "NEEDS_DIAGNOSTIC"}: return "BLOCKED"
    return "ACTIVE"


def _summarize(campaign: dict[str, Any], state: dict[str, Any]) -> None:
    for view_key in campaign["view_registry"]:
        jobs = [j for j in state["jobs"].values() if j["view_key"] == view_key]
        state["view_coverage"][view_key] = {s: sum(j["status"] == s for j in jobs) for s in STATES if any(j["status"] == s for j in jobs)}
    for stage in campaign["stages"]:
        jobs = [j for j in state["jobs"].values() if j["stage_id"] == stage["stage_id"]]
        state["stage_status"][stage["stage_id"]] = _stage_status(jobs)
    state["blocked_jobs"] = {j["job_id"]: j["status_reason"] for j in state["jobs"].values() if j["status"] in {"BLOCKED", "NEEDS_DIAGNOSTIC"}}
    state["updated_at"] = utc_now()


def ingest_manifests(campaign: dict[str, Any], state: dict[str, Any], manifests: Iterable[dict[str, Any]]) -> None:
    _assert_binding(campaign, state)
    for manifest in manifests:
        if manifest.get("campaign_id") != state["campaign_id"]: raise CampaignError("manifest campaign mismatch")
        run_id = manifest.get("run_id"); digest = canonical_fingerprint(manifest)
        if not run_id: raise CampaignError("manifest has no run_id")
        prior = state["ingested_revit_runs"].get(run_id)
        if prior:
            if prior["fingerprint"] != digest: _conflict(state, "RUN_CONFLICT", run_id, prior["fingerprint"], digest)
            continue
        state["ingested_revit_runs"][run_id] = {"fingerprint": digest, "batch_id": manifest.get("batch_id"), "source": manifest.get("batch_source"), "ingested_at": utc_now()}
        for result in manifest.get("jobs", []):
            job = state["jobs"].get(result.get("job_id"))
            if not job: _conflict(state, "UNKNOWN_JOB", result.get("job_id"), None, run_id); continue
            expected = job.get("execution_fingerprints", {}).get(manifest.get("batch_id"))
            if not expected or result.get("configuration_fingerprint") != expected:
                _conflict(state, "JOB_CONFIGURATION_DRIFT", job["job_id"], expected, result.get("configuration_fingerprint")); continue
            status = result.get("execution_status")
            if run_id not in job["run_ids"]: job["run_ids"].append(run_id)
            envelope = result.get("raw_result_envelope")
            if status == "failed" and envelope is None and job["status"] == "BATCHED":
                # The analyzer intentionally skips manifest jobs without an envelope.
                # Finalize this executor-level failure so it cannot wait forever for
                # analysis that can never be produced.
                transition(state, job["job_id"], "FAILED", "REVIT_EXECUTION_FAILED",
                           {"run_id": run_id, "execution_status": status,
                            "errors": result.get("errors", [])})
            elif status in {"completed", "inconclusive", "failed"} and job["status"] == "BATCHED":
                transition(state, job["job_id"], "EXECUTED", "REVIT_EXECUTION_RECORDED",
                           {"run_id": run_id, "execution_status": status})
            envelope = envelope or {}
            for artifact in envelope.get("artifact_paths", []):
                if artifact not in job["artifact_references"]: job["artifact_references"].append(artifact)
            # A job can go straight to terminal FAILED here (envelope-less
            # executor failure) without ever reaching ingest_analysis, which
            # is otherwise the only place evidence gets synced. Without this,
            # a manual-review requirement on such a job would stay an
            # accept/reject-able-looking PENDING forever despite having no
            # artifact and no future analysis record that could ever supply
            # one.
            _sync_review_evidence(state, job)
        _event(state, "REVIT_RUN_INGESTED", run_id=run_id, fingerprint=digest)
    evaluate(campaign, state)


def _raster_artifacts(paths: Iterable[Any]) -> list[str]:
    """Return only the entries naming a concrete reviewable raster image.

    A manual/visual review requirement must never be treated as satisfiable
    by a JSON sidecar, a log path, or any other non-image reference - only a
    real PNG/TIFF/JPEG an operator can actually open and look at counts as
    "reviewable" evidence.
    """
    return [str(p) for p in paths if isinstance(p, str) and p.lower().endswith(RASTER_ARTIFACT_SUFFIXES)]


def _sync_review_evidence(state: dict[str, Any], job: dict[str, Any]) -> None:
    """Keep a manual review requirement's own evidence in step with the job's
    artifact_references, and classify - rather than silently allow - the
    case where a job needing review never produced anything reviewable.

    This is the one place both kinds of requirement (campaign-authored
    ``manual_review_required`` and the gate-scoped automated one) pick up
    real evidence: a fresh reviewable artifact is merged in immediately, and
    a requirement still PENDING once its job is terminal with nothing
    reviewable is downgraded to EVIDENCE_MISSING with an explanatory
    diagnostic rather than left as an accept/reject-able PENDING review that
    has nothing behind it (see record_manual_review, which refuses to
    resolve EVIDENCE_MISSING requirements).
    """
    requirement = state["manual_review_requirements"].get(job["job_id"])
    if requirement is None:
        return
    reviewable = _raster_artifacts(job.get("artifact_references") or [])
    if reviewable:
        merged = list(requirement.get("artifact_references") or [])
        for path in reviewable:
            if path not in merged:
                merged.append(path)
        requirement["artifact_references"] = merged
        if requirement["status"] == "EVIDENCE_MISSING":
            requirement["status"] = "PENDING"
    elif job["status"] in TERMINAL and requirement["status"] == "PENDING":
        requirement["status"] = "EVIDENCE_MISSING"
        requirement.setdefault("artifact_references", [])
        requirement["diagnostic"] = (
            "This job reached a terminal state while a manual review was still required, but no "
            "reviewable raster (PNG/TIFF/JPEG) artifact was ever recorded in artifact_references. "
            "Manual review cannot be accepted or rejected until a reviewable artifact is produced "
            "and ingested for this job.")


def _conflict(state: dict[str, Any], kind: str, identity: Any, existing: Any, incoming: Any) -> None:
    item = {"code": kind, "identity": identity, "existing": existing, "incoming": incoming}
    if item not in state["conflicts"]: state["conflicts"].append(item); _event(state, "CONFLICT", **item)


def ingest_analysis(campaign: dict[str, Any], state: dict[str, Any], inputs: Iterable[dict[str, Any]]) -> None:
    _assert_binding(campaign, state)
    records = []
    for value in inputs: records.extend(value.get("acceptance_records", [])) if isinstance(value.get("acceptance_records"), list) else records.append(value)
    for record in records:
        record_id = canonical_fingerprint({k: record.get(k) for k in ("campaign_id", "batch_id", "run_id", "job_id", "probe_id", "analyzer_version", "source_report")})
        digest = canonical_fingerprint(record); prior = state["ingested_analysis_records"].get(record_id)
        if prior:
            if prior["fingerprint"] != digest: _conflict(state, "ANALYSIS_CONFLICT", record_id, prior["fingerprint"], digest)
            continue
        job = state["jobs"].get(record.get("job_id"))
        if not job: _conflict(state, "UNKNOWN_ANALYSIS_JOB", record.get("job_id"), None, record_id); continue
        if record.get("run_id") not in job["run_ids"]: _conflict(state, "ANALYSIS_RUN_NOT_INGESTED", record_id, job["run_ids"], record.get("run_id")); continue
        if job["status"] != "EXECUTED": _conflict(state, "ANALYSIS_STATE_CONFLICT", record_id, job["status"], "EXECUTED"); continue
        state["ingested_analysis_records"][record_id] = {"fingerprint": digest, "job_id": job["job_id"], "run_id": record.get("run_id"), "source_report": record.get("source_report"), "artifact_references": record.get("artifact_references", []), "ingested_at": utc_now()}
        job["analysis_record_ids"].append(record_id)
        transition(state, job["job_id"], "ANALYZED", "NORMALIZED_ANALYSIS_INGESTED", {"record_id": record_id})
        acceptance = record.get("acceptance_status")
        target = {"PASS": "PASSED", "FAIL": "FAILED", "INCONCLUSIVE": "INCONCLUSIVE"}.get(acceptance)
        if not target: raise CampaignError(f"invalid acceptance_status: {acceptance}")
        reason_codes = record.get("reason_codes", [])
        if record.get("execution_status") == "failed" or any(c in reason_codes for c in ("ROLLBACK_FAILED", "RESTORATION_FAILED")): target = "FAILED"
        transition(state, job["job_id"], target, "NORMALIZED_ACCEPTANCE", {"acceptance_status": acceptance, "reason_codes": reason_codes})
        # A gate-scoped manual review requirement: distinct from the campaign-
        # authored manual_review_required flag, but the two can coincide on
        # the same job (a job flagged manual_review_required that also ends
        # up INCONCLUSIVE for MANUAL_SEMANTIC_REVIEW_REQUIRED). `setdefault`
        # merges the gate marker into whichever requirement dict already
        # exists rather than skipping seeding entirely when one does - a
        # campaign-authored requirement predates this and has no "gate" key,
        # so without merging, record_manual_review's gate check would never
        # match and an ACCEPTED review could never transition the job out of
        # INCONCLUSIVE, permanently stranding its dependents. It never
        # becomes a blanket override of unrelated FAIL/INCONCLUSIVE findings
        # on the same job (see record_manual_review). Seeded only when
        # MANUAL_SEMANTIC_REVIEW_REQUIRED is the *sole* reason: a mixed set
        # (e.g. alongside NO_CASES_ANALYZED) is not this gate's to resolve,
        # and record_manual_review only ever resolves a job whose reason
        # codes are exactly this one - seeding a requirement it could never
        # satisfy would leave the operator with an unresolvable review.
        #
        # An analyzer concluding it cannot automatically verify semantic
        # preservation is not, by itself, an operator review task: without a
        # reviewable raster artifact already on the job, seeding an
        # actionable PENDING requirement here would let record_manual_review
        # be asked to rubber-stamp ACCEPTED with nothing to look at. When no
        # requirement exists yet, seed EVIDENCE_MISSING (never PENDING)
        # instead, using diagnostic-only wording so this never reads to an
        # operator as "manual review required".
        if target == "INCONCLUSIVE" and set(reason_codes) == {"MANUAL_SEMANTIC_REVIEW_REQUIRED"}:
            if job["job_id"] not in state["manual_review_requirements"]:
                reviewable_now = _raster_artifacts(job.get("artifact_references") or [])
                if reviewable_now:
                    state["manual_review_requirements"][job["job_id"]] = {
                        "status": "PENDING", "reason": "MANUAL_SEMANTIC_REVIEW_REQUIRED",
                        "artifact_references": list(reviewable_now),
                    }
                else:
                    state["manual_review_requirements"][job["job_id"]] = {
                        "status": "EVIDENCE_MISSING", "reason": "SEMANTIC_PRESERVATION_NOT_AUTOMATICALLY_VERIFIED",
                        "artifact_references": [],
                        "diagnostic": ("An automated check could not verify rendered semantic preservation, "
                                       "and no reviewable raster artifact (PNG/TIFF/JPEG) was recorded for "
                                       "this job, so no operator review task was created."),
                    }
            requirement = state["manual_review_requirements"][job["job_id"]]
            requirement.setdefault("gate", "rendered_semantic_preservation")
            requirement.setdefault("record_id", record_id)
        _sync_review_evidence(state, job)
        _event(state, "ANALYSIS_INGESTED", record_id=record_id, job_id=job["job_id"], fingerprint=digest)
    evaluate(campaign, state)


def generate_next_batch(campaign: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    _assert_binding(campaign, state); evaluate(campaign, state)
    eligible = sorted((j for j in state["jobs"].values() if j["status"] == "ELIGIBLE"), key=lambda j: (j["stage_order"], j["job_order"]))
    if not eligible:
        state["next_recommendation"] = compute_next_recommendation(state)
        return None
    defaults = campaign["execution_defaults"]; selected = eligible[:defaults["batch_size"]]
    sequence = len({b for j in state["jobs"].values() for b in j["batch_ids"]}) + 1
    batch_id = f"{campaign['campaign_id']}.batch.{sequence:04d}"
    fingerprint = state["campaign_configuration_fingerprint"]
    batch = {"schema_version": "1.0", "campaign_id": campaign["campaign_id"], "batch_id": batch_id,
        "created_at": utc_now(), "campaign_configuration_fingerprint": fingerprint,
        "document": copy.deepcopy(campaign["document_expectations"]),
        "execution_policy": {"max_jobs_per_run": defaults["batch_size"], "on_job_error": defaults.get("on_job_error", "continue"), "resume": True, "allow_rerun": False}, "jobs": []}
    for item in selected:
        view = campaign["view_registry"][item["view_key"]]
        # NOTE: planner-owned identity fingerprints are batch-job-level metadata,
        # never probe-facing settings. They must not ride inside `settings`,
        # which the executor's adapter forwards almost verbatim to the probe.
        settings = copy.deepcopy(item["settings"])
        batch_job = {"job_id": item["job_id"], "probe_id": item["probe_id"], "variant": item["variant"],
            "view": {"unique_id": view["unique_id"], "name": view["expected_name"], "view_type": view["expected_view_type"], "crop_active": view["expected_crop_active"]},
            "settings": settings, "output_directory": item.get("output_directory", f"raw/{item['job_id']}"),
            "job_configuration_fingerprint": item["configuration_fingerprint"]}
        batch["jobs"].append(batch_job)
        # Must match the executor's job_fingerprint() exactly: the same
        # dispatch-identity fields, in the same canonical encoding. `variant`
        # IS dispatch identity now (the adapter maps it to the probe's
        # selection kwarg whenever `settings` doesn't already carry an
        # explicit `selection`), so it must be included - otherwise a
        # hand-edited/corrupted batch could change which mutations actually
        # run while keeping the fingerprint ingestion/resume checks expect.
        # `job_configuration_fingerprint` is batch-job metadata, not dispatch
        # identity, and stays excluded.
        execution_material = {key: batch_job.get(key) for key in ("job_id", "probe_id", "view", "settings", "output_directory", "variant")}
        item["execution_fingerprints"][batch_id] = canonical_fingerprint(execution_material)
        transition(state, item["job_id"], "BATCHED", "SELECTED_FOR_BATCH", {"batch_id": batch_id}); item["batch_ids"].append(batch_id)
    state["next_recommendation"] = {"code": "RUN_BATCH_IN_REVIT", "batch_id": batch_id, "job_count": len(selected)}
    return batch


def diagnostic_action(campaign: dict[str, Any], state: dict[str, Any], job_id: str,
                      action: str, rule: str | None = None) -> None:
    _assert_binding(campaign, state); job = state["jobs"].get(job_id)
    if not job: raise CampaignError("unknown job_id")
    if action == "request":
        allowed = {r["rule_id"] for r in campaign["diagnostic_expansion_rules"]}
        if rule not in allowed: raise CampaignError("diagnostic rule is not authorized by campaign")
        configured = next(r for r in campaign["diagnostic_expansion_rules"] if r["rule_id"] == rule)
        variants = configured.get("variants")
        if not isinstance(variants, list) or not variants:
            raise CampaignError("diagnostic rule must define a non-empty variants expansion")
        state["diagnostic_requests"][job_id] = {"rule_id": rule, "status": "AUTHORIZED", "requested_at": utc_now()}
        if job["status"] in {"FAILED", "INCONCLUSIVE", "BLOCKED"}: transition(state, job_id, "NEEDS_DIAGNOSTIC", "AUTHORIZED_DIAGNOSTIC", {"rule_id": rule})
        for index, variant in enumerate(variants, 1):
            diagnostic = copy.deepcopy(job)
            diagnostic["job_key"] = f"diagnostic.{job['job_key']}.{rule}.{variant}"
            # The source ID is identity material: two repetitions or variants in
            # the same view/probe must receive distinct diagnostic expansions.
            diagnostic["case"] = f"diagnostic_{rule}_{job['job_id']}"; diagnostic["variant"] = str(variant)
            diagnostic["repetition"] = index; diagnostic["job_order"] = job["job_order"] + index / 1000.0
            diagnostic["job_id"] = stable_job_id(campaign["campaign_id"], job["stage_id"], job["view_key"], job["probe_id"], diagnostic["case"], diagnostic["variant"], index)
            if diagnostic["job_id"] in state["jobs"]: continue
            diagnostic["settings"] = {**job["settings"], "diagnostic_rule": rule, "diagnostic_variant": variant}
            diagnostic["configuration_fingerprint"] = canonical_fingerprint(diagnostic)
            diagnostic.update({"status": "PLANNED", "status_reason": {"code": "AUTHORIZED_DIAGNOSTIC"}, "batch_ids": [], "run_ids": [], "analysis_record_ids": [], "artifact_references": [], "execution_fingerprints": {}})
            state["jobs"][diagnostic["job_id"]] = diagnostic
    elif action == "clear":
        if job_id in state["diagnostic_requests"]: state["diagnostic_requests"][job_id]["status"] = "CLEARED"
    elif action == "reset":
        if job["status"] not in TERMINAL | {"BLOCKED", "NEEDS_DIAGNOSTIC"}: raise CampaignError("only completed or blocked jobs may be reset")
        transition(state, job_id, "SUPERSEDED", "AUTHORIZED_RESET")
    else: raise CampaignError("action must be request, clear, or reset")
    evaluate(campaign, state)


def record_manual_review(campaign: dict[str, Any], state: dict[str, Any], job_id: str,
                         outcome: str, reviewer: str) -> None:
    _assert_binding(campaign, state)
    if job_id not in state["manual_review_requirements"]: raise CampaignError("job has no manual review requirement")
    if outcome not in {"ACCEPTED", "REJECTED"}: raise CampaignError("manual outcome must be ACCEPTED or REJECTED")
    requirement = state["manual_review_requirements"][job_id]
    reviewable = _raster_artifacts(requirement.get("artifact_references") or [])
    if not reviewable:
        # Never let ACCEPTED/REJECTED stand in for evidence that does not
        # exist: an operator with nothing to look at must get an explicit,
        # actionable refusal, not a rubber-stamped review record.
        raise CampaignError(
            "NO_REVIEWABLE_ARTIFACT: cannot record manual review {0!r} for {1} - its requirement carries no "
            "reviewable raster (PNG/TIFF/JPEG) artifact_references (current requirement status: {2!r}). "
            "Produce and ingest the missing artifact before recording a decision.".format(
                outcome, job_id, requirement.get("status")))
    requirement.update({"status": outcome, "reviewer": reviewer, "reviewed_at": utc_now()})
    _event(state, "MANUAL_REVIEW_RECORDED", job_id=job_id, outcome=outcome, reviewer=reviewer)
    # Gate-scoped reviews (seeded by ingest_analysis for MANUAL_SEMANTIC_REVIEW_REQUIRED)
    # resolve only the job they were seeded for, and only when that specific gate
    # was the job's sole blocking reason - never a blanket override of an
    # unrelated FAIL/INCONCLUSIVE finding recorded on the same job.
    if requirement.get("gate") == "rendered_semantic_preservation":
        job = state["jobs"].get(job_id)
        if job and job["status"] == "INCONCLUSIVE":
            reason_codes = set(((job.get("status_reason") or {}).get("details") or {}).get("reason_codes") or [])
            if reason_codes == {"MANUAL_SEMANTIC_REVIEW_REQUIRED"}:
                target = "PASSED" if outcome == "ACCEPTED" else "FAILED"
                transition(state, job_id, target, "MANUAL_REVIEW_" + outcome, {"record_id": requirement.get("record_id"), "reviewer": reviewer})
                evaluate(campaign, state)
                return
    _summarize(campaign, state)


def compute_next_recommendation(state: dict[str, Any]) -> dict[str, Any]:
    """Derive the operator's next step from current state without mutating it."""
    statuses = {j["status"] for j in state["jobs"].values()}
    eligible = [j["job_id"] for j in state["jobs"].values() if j["status"] == "ELIGIBLE"]
    rejected = sorted(job_id for job_id, review in state["manual_review_requirements"].items()
                      if review["status"] == "REJECTED")
    evidence_missing = sorted(job_id for job_id, review in state["manual_review_requirements"].items()
                              if review["status"] == "EVIDENCE_MISSING")
    pending_review = any(review["status"] == "PENDING"
                         for review in state["manual_review_requirements"].values())
    if state["conflicts"]: return {"code": "INVALID_CAMPAIGN_STATE"}
    if eligible: return {"code": "GENERATE_NEXT_BATCH", "eligible_job_count": len(eligible)}
    if rejected: return {"code": "BLOCKED_BY_FAILURE", "reason": "MANUAL_REVIEW_REJECTED", "job_ids": rejected}
    # A requirement stuck at EVIDENCE_MISSING can never be resolved by
    # record_manual_review (see there): the campaign must never report
    # COMPLETE, and this must never be mistaken for an ordinary actionable
    # pending review, while one exists.
    if evidence_missing: return {"code": "BLOCKED_BY_FAILURE", "reason": "MANUAL_REVIEW_EVIDENCE_MISSING", "job_ids": evidence_missing}
    if statuses <= TERMINAL and pending_review: return {"code": "AWAITING_MANUAL_REVIEW"}
    if statuses <= TERMINAL: return {"code": "CAMPAIGN_COMPLETE"}
    if "BATCHED" in statuses: return {"code": "AWAITING_REVIT_EXECUTION"}
    if "EXECUTED" in statuses or "ANALYZED" in statuses: return {"code": "AWAITING_EXTERNAL_ANALYSIS"}
    if pending_review: return {"code": "AWAITING_MANUAL_REVIEW"}
    return {"code": "BLOCKED_BY_FAILURE"}


def migrate_state(state: dict[str, Any]) -> dict[str, Any]:
    """Backfill structurally-required keys added by newer planner code.

    This never touches job statuses, history, run/analysis linkage, or any
    other evidence already recorded in the state file - it only adds missing
    containers (e.g. ``closures``, introduced for conditional Stage 1
    mutation-closure fallbacks) so an older on-disk ``campaign_state.json``
    keeps loading under newer planner code. Idempotent; safe to call on an
    already-migrated state.

    A requirement written by a pre-evidence-guard planner has no
    ``artifact_references`` field at all, even when its job already carries
    real raster artifacts - and because evidence normally syncs only when a
    *new* analysis record is ingested, re-ingesting an already-recorded
    analysis is a no-op duplicate that would never backfill it, permanently
    stranding an in-progress campaign's still-open reviews behind the new
    guard. A still-``PENDING`` requirement is therefore resynced from its
    job's current evidence here (never touching job status, only the
    requirement's own artifact_references/status via ``_sync_review_evidence``,
    the same function ``ingest_analysis``/``ingest_manifests`` use). A
    decision already recorded (``ACCEPTED``/``REJECTED``) is never touched -
    its evidentiary status is instead surfaced read-only, without rewriting
    it, by ``manual_review_evidence_warnings``.
    """
    state = dict(state)
    state.setdefault("closures", {})
    for job_id, requirement in state.get("manual_review_requirements", {}).items():
        requirement.setdefault("artifact_references", [])
        if requirement.get("status") == "PENDING":
            job = state.get("jobs", {}).get(job_id)
            if job is not None:
                _sync_review_evidence(state, job)
    return state


def manual_review_evidence_warnings(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Read-only: flag any recorded manual-review decision whose requirement
    carries no reviewable raster artifact_references.

    This never rewrites historical state - an ACCEPTED/REJECTED record made
    before this evidentiary check existed is preserved exactly as recorded
    (migrate_state never touches it either) - it only makes that record's
    invalid/incomplete evidentiary status explicit wherever state is read or
    summarized.
    """
    warnings = []
    for job_id, requirement in state["manual_review_requirements"].items():
        status = requirement.get("status")
        if status in {"ACCEPTED", "REJECTED"} and not _raster_artifacts(requirement.get("artifact_references") or []):
            warnings.append({"job_id": job_id, "status": status,
                             "issue": "DECISION_RECORDED_WITHOUT_REVIEWABLE_ARTIFACT"})
    return warnings


def status_summary(campaign: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    if (state.get("campaign_id") != campaign.get("campaign_id") or
            state.get("campaign_configuration_fingerprint") != canonical_fingerprint(campaign)):
        recommendation = {"code": "INVALID_CAMPAIGN_STATE", "reason": "CAMPAIGN_STATE_MISMATCH"}
    else:
        recommendation = compute_next_recommendation(state)
    return {"campaign_id": state["campaign_id"], "jobs": {s: sum(j["status"] == s for j in state["jobs"].values()) for s in STATES},
            "stages": state["stage_status"], "coverage": state["view_coverage"],
            "conflicts": state["conflicts"], "next_recommendation": recommendation,
            "manual_review_evidence_warnings": manual_review_evidence_warnings(state)}


def package_campaign(campaign: dict[str, Any], state: dict[str, Any], output_dir: str | Path,
                     *, source_root: str | Path | None = None) -> dict[str, Any]:
    """Copy every reviewable artifact referenced by a pending or completed
    (PENDING/ACCEPTED/REJECTED) manual review requirement into ``output_dir``,
    alongside a self-contained copy of the campaign and state, so a packaged
    campaign can be reviewed without access to the original evidence/scratch
    directory. Never packages a dangling reference: a referenced artifact
    that cannot be found on disk fails the whole operation loudly rather than
    silently omitting it or shipping a state that points at nothing.
    """
    if (state.get("campaign_id") != campaign.get("campaign_id") or
            state.get("campaign_configuration_fingerprint") != canonical_fingerprint(campaign)):
        raise CampaignError("campaign/state mismatch; cannot package")
    output_dir = Path(output_dir)
    artifacts_root = output_dir / "artifacts" / "manual_review"
    packaged: dict[str, list[str]] = {}
    packaged_state = copy.deepcopy(state)
    for job_id, requirement in state["manual_review_requirements"].items():
        if requirement.get("status") not in {"PENDING", "ACCEPTED", "REJECTED"}:
            continue
        refs = _raster_artifacts(requirement.get("artifact_references") or [])
        packaged_paths = []
        for index, ref in enumerate(refs):
            source = Path(ref)
            if source_root is not None and not source.is_absolute():
                source = Path(source_root) / source
            if not source.is_file():
                raise CampaignError(
                    "MISSING_ARTIFACT_SOURCE: manual review artifact for {0} not found on disk: {1}".format(
                        job_id, source))
            # Index-scoped per artifact: two references can share a filename
            # while coming from different source directories (e.g. two
            # resolution/run subfolders), and flattening both to a bare
            # source.name would let the second copy silently overwrite the
            # first, losing distinct review evidence.
            job_dir = artifacts_root / job_id / str(index)
            job_dir.mkdir(parents=True, exist_ok=True)
            dest = job_dir / source.name
            shutil.copy2(str(source), str(dest))
            packaged_paths.append(str(Path("artifacts") / "manual_review" / job_id / str(index) / source.name))
        packaged[job_id] = packaged_paths
        packaged_state["manual_review_requirements"][job_id]["artifact_references"] = packaged_paths
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(output_dir / "campaign.json", campaign)
    atomic_write(output_dir / "campaign_state.json", packaged_state)
    return {"output_dir": str(output_dir), "packaged_artifacts": packaged}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="External deterministic Stage A campaign planner")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "init", "migrate", "status", "package", "next-batch", "ingest-runs", "ingest-analysis", "diagnostic", "manual-review"):
        p = sub.add_parser(name); p.add_argument("campaign")
        if name != "validate": p.add_argument("state")
        if name in {"ingest-runs", "ingest-analysis"}: p.add_argument("inputs", nargs="+")
        if name == "next-batch": p.add_argument("output")
        if name == "package": p.add_argument("output_dir"); p.add_argument("--source-root")
        if name == "diagnostic": p.add_argument("action", choices=("request", "clear", "reset")); p.add_argument("job_id"); p.add_argument("--rule")
        if name == "manual-review": p.add_argument("job_id"); p.add_argument("outcome", choices=("ACCEPTED", "REJECTED")); p.add_argument("reviewer")
    args = parser.parse_args(argv); campaign = validate_campaign(load_json(args.campaign))
    if args.command == "validate": print(json.dumps({"valid": True, "fingerprint": canonical_fingerprint(campaign)})); return 0
    if args.command == "init": atomic_write(args.state, initialize_state(campaign)); return 0
    if args.command == "migrate":
        # In-place structural backfill only; never reconciles evidence against a
        # changed campaign document. Follow with `status` to see whether the
        # campaign itself has since drifted (CONFIGURATION_DRIFT supersedes,
        # it never silently discards evidence already on disk).
        atomic_write(args.state, migrate_state(load_json(args.state))); return 0
    state = migrate_state(load_json(args.state))
    if args.command == "status": print(json.dumps(status_summary(campaign, state), indent=2, sort_keys=True)); return 0
    if args.command == "package":
        result = package_campaign(campaign, state, args.output_dir, source_root=args.source_root)
        print(json.dumps(result, indent=2, sort_keys=True)); return 0
    try:
        if args.command == "ingest-runs": ingest_manifests(campaign, state, map(load_json, args.inputs))
        elif args.command == "ingest-analysis": ingest_analysis(campaign, state, map(load_json, args.inputs))
        elif args.command == "diagnostic": diagnostic_action(campaign, state, args.job_id, args.action, args.rule)
        elif args.command == "manual-review": record_manual_review(campaign, state, args.job_id, args.outcome, args.reviewer)
        elif args.command == "next-batch":
            batch = generate_next_batch(campaign, state)
            if batch: atomic_write(args.output, batch)
            else: print(json.dumps(state["next_recommendation"]))
    except ConfigurationDriftError:
        # CONFIGURATION_DRIFT is the one CampaignError whose in-memory
        # mutation (every job superseded, the drift event recorded) is
        # complete and self-contained by construction - see
        # ConfigurationDriftError's docstring - so it is the only case where
        # persisting after an error is safe. Any other CampaignError (a
        # malformed manifest partway through a multi-manifest ingest, an
        # invalid analysis record, an unauthorized diagnostic request, ...)
        # is deliberately NOT caught here: it propagates without writing
        # anything, leaving the on-disk state exactly as it was. The
        # operator is still told about the drift (this re-raises to a
        # non-zero exit); nothing already applied is silently discarded
        # either way.
        atomic_write(args.state, state)
        raise
    atomic_write(args.state, state); return 0


if __name__ == "__main__":
    sys.exit(main())
