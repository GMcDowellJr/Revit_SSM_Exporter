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
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "1.0"
STATES = ("PLANNED", "ELIGIBLE", "BATCHED", "EXECUTED", "ANALYZED", "PASSED",
          "FAILED", "INCONCLUSIVE", "BLOCKED", "NEEDS_DIAGNOSTIC", "SKIPPED",
          "SUPERSEDED")
TERMINAL = {"PASSED", "FAILED", "INCONCLUSIVE", "SKIPPED", "SUPERSEDED"}
LEGAL_TRANSITIONS = {
    "PLANNED": {"ELIGIBLE", "BLOCKED", "SKIPPED", "SUPERSEDED", "NEEDS_DIAGNOSTIC"},
    "ELIGIBLE": {"BATCHED", "BLOCKED", "SKIPPED", "SUPERSEDED"},
    "BATCHED": {"EXECUTED", "ELIGIBLE", "SUPERSEDED"},
    "EXECUTED": {"ANALYZED", "FAILED", "INCONCLUSIVE", "SUPERSEDED"},
    "ANALYZED": {"PASSED", "FAILED", "INCONCLUSIVE", "SUPERSEDED"},
    "FAILED": {"NEEDS_DIAGNOSTIC", "SUPERSEDED"},
    "INCONCLUSIVE": {"NEEDS_DIAGNOSTIC", "SUPERSEDED"},
    "BLOCKED": {"ELIGIBLE", "SKIPPED", "SUPERSEDED", "NEEDS_DIAGNOSTIC"},
    "NEEDS_DIAGNOSTIC": {"ELIGIBLE", "BLOCKED", "SUPERSEDED"},
    "PASSED": {"SUPERSEDED"}, "SKIPPED": {"SUPERSEDED"}, "SUPERSEDED": set(),
}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CampaignError(ValueError):
    """Invalid campaign, state, or evidence."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_fingerprint(value: Any) -> str:
    """Hash canonical JSON, excluding descriptive notes and volatile timestamps."""
    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {k: clean(v) for k, v in sorted(item.items())
                    if k not in {"notes", "created_at", "updated_at"}}
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
    for dep in campaign["dependencies"]:
        if dep.get("job") not in job_keys or dep.get("requires_job") not in job_keys:
            raise CampaignError(f"dependency references unknown job: {dep}")
        if dep.get("statuses", ["PASS"]) and not set(dep.get("statuses", ["PASS"])) <= {"PASS", "FAIL", "INCONCLUSIVE"}:
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
        "diagnostic_requests": {}, "manual_review_requirements": {}, "next_recommendation": {}, "history": []}
    for job in _expanded_jobs(campaign):
        state["jobs"][job["job_id"]] = {**job, "status": "PLANNED",
            "status_reason": {"code": "INITIALIZED"}, "batch_ids": [], "run_ids": [],
            "analysis_record_ids": [], "artifact_references": [], "execution_fingerprints": {}}
        if job.get("manual_review_required"):
            state["manual_review_requirements"][job["job_id"]] = {"status": "PENDING", "reason": job.get("manual_review_reason", "CONFIGURED")}
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
        raise CampaignError("campaign configuration drift; prior jobs were superseded, initialize amended state")


def _dependencies(campaign: dict[str, Any], job: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for d in campaign["dependencies"] if d["job"] == job["job_key"]]


def evaluate(campaign: dict[str, Any], state: dict[str, Any]) -> None:
    """Recompute deterministic gates without regressing batched or completed work."""
    key_map = {j["job_key"]: j for j in state["jobs"].values()}
    for job in sorted(state["jobs"].values(), key=lambda j: (j["stage_order"], j["job_order"])):
        if job["status"] not in {"PLANNED", "ELIGIBLE", "BLOCKED", "NEEDS_DIAGNOSTIC"}: continue
        deps = _dependencies(campaign, job)
        failures, waiting = [], []
        for dep in deps:
            upstream = key_map[dep["requires_job"]]
            wanted = set(dep.get("statuses", ["PASS"]))
            actual = {"PASSED": "PASS", "FAILED": "FAIL", "INCONCLUSIVE": "INCONCLUSIVE"}.get(upstream["status"])
            if actual in wanted: continue
            if actual in {"FAIL", "INCONCLUSIVE"} or upstream["status"] in {"BLOCKED", "NEEDS_DIAGNOSTIC", "SKIPPED", "SUPERSEDED"}:
                failures.append({"requires_job": upstream["job_id"], "actual": actual or upstream["status"], "wanted": sorted(wanted)})
            else: waiting.append({"requires_job": upstream["job_id"], "status": upstream["status"]})
        target, reason, detail = ("BLOCKED", "DEPENDENCY_FAILED", failures) if failures else (("PLANNED", "AWAITING_DEPENDENCY", waiting) if waiting else ("ELIGIBLE", "DEPENDENCIES_SATISFIED", []))
        if target != job["status"]:
            if target == "PLANNED" and job["status"] in {"BLOCKED", "NEEDS_DIAGNOSTIC"}: continue
            transition(state, job["job_id"], target, reason, detail)
        elif target == "PLANNED": job["status_reason"] = {"code": reason, "details": detail}
    _summarize(campaign, state)


def _summarize(campaign: dict[str, Any], state: dict[str, Any]) -> None:
    for view_key in campaign["view_registry"]:
        jobs = [j for j in state["jobs"].values() if j["view_key"] == view_key]
        state["view_coverage"][view_key] = {s: sum(j["status"] == s for j in jobs) for s in STATES if any(j["status"] == s for j in jobs)}
    for stage in campaign["stages"]:
        jobs = [j for j in state["jobs"].values() if j["stage_id"] == stage["stage_id"]]
        state["stage_status"][stage["stage_id"]] = "COMPLETE" if jobs and all(j["status"] in TERMINAL for j in jobs) else ("BLOCKED" if jobs and all(j["status"] in TERMINAL | {"BLOCKED", "NEEDS_DIAGNOSTIC"} for j in jobs) else "ACTIVE")
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
            if status in {"completed", "inconclusive", "failed"} and job["status"] == "BATCHED":
                transition(state, job["job_id"], "EXECUTED", "REVIT_EXECUTION_RECORDED", {"run_id": run_id, "execution_status": status})
            if run_id not in job["run_ids"]: job["run_ids"].append(run_id)
            envelope = result.get("raw_result_envelope") or {}
            for artifact in envelope.get("artifact_paths", []):
                if artifact not in job["artifact_references"]: job["artifact_references"].append(artifact)
        _event(state, "REVIT_RUN_INGESTED", run_id=run_id, fingerprint=digest)
    evaluate(campaign, state)


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
        if record.get("execution_status") == "failed" or any(c in record.get("reason_codes", []) for c in ("ROLLBACK_FAILED", "RESTORATION_FAILED")): target = "FAILED"
        transition(state, job["job_id"], target, "NORMALIZED_ACCEPTANCE", {"acceptance_status": acceptance, "reason_codes": record.get("reason_codes", [])})
        _event(state, "ANALYSIS_INGESTED", record_id=record_id, job_id=job["job_id"], fingerprint=digest)
    evaluate(campaign, state)


def generate_next_batch(campaign: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    _assert_binding(campaign, state); evaluate(campaign, state)
    eligible = sorted((j for j in state["jobs"].values() if j["status"] == "ELIGIBLE"), key=lambda j: (j["stage_order"], j["job_order"]))
    if not eligible:
        statuses = {j["status"] for j in state["jobs"].values()}
        if state["conflicts"]: code = "INVALID_CAMPAIGN_STATE"
        elif statuses <= TERMINAL and any(v["status"] == "PENDING" for v in state["manual_review_requirements"].values()): code = "AWAITING_MANUAL_REVIEW"
        elif statuses <= TERMINAL: code = "CAMPAIGN_COMPLETE"
        elif "BATCHED" in statuses: code = "AWAITING_REVIT_EXECUTION"
        elif "EXECUTED" in statuses or "ANALYZED" in statuses: code = "AWAITING_EXTERNAL_ANALYSIS"
        elif any(v["status"] == "PENDING" for v in state["manual_review_requirements"].values()): code = "AWAITING_MANUAL_REVIEW"
        else: code = "BLOCKED_BY_FAILURE"
        state["next_recommendation"] = {"code": code}; return None
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
        settings = copy.deepcopy(item["settings"]); settings["campaign_configuration_fingerprint"] = fingerprint; settings["job_configuration_fingerprint"] = item["configuration_fingerprint"]
        batch_job = {"job_id": item["job_id"], "probe_id": item["probe_id"],
            "view": {"unique_id": view["unique_id"], "name": view["expected_name"], "view_type": view["expected_view_type"], "crop_active": view["expected_crop_active"]},
            "settings": settings, "output_directory": item.get("output_directory", f"raw/{item['job_id']}")}
        batch["jobs"].append(batch_job)
        item["execution_fingerprints"][batch_id] = canonical_fingerprint(batch_job)
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
        state["diagnostic_requests"][job_id] = {"rule_id": rule, "status": "AUTHORIZED", "requested_at": utc_now()}
        if job["status"] in {"FAILED", "INCONCLUSIVE", "BLOCKED"}: transition(state, job_id, "NEEDS_DIAGNOSTIC", "AUTHORIZED_DIAGNOSTIC", {"rule_id": rule})
        configured = next(r for r in campaign["diagnostic_expansion_rules"] if r["rule_id"] == rule)
        for index, variant in enumerate(configured.get("variants", []), 1):
            diagnostic = copy.deepcopy(job)
            diagnostic["job_key"] = f"diagnostic.{job['job_key']}.{rule}.{variant}"
            diagnostic["case"] = f"diagnostic_{rule}"; diagnostic["variant"] = str(variant)
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
    state["manual_review_requirements"][job_id].update({"status": outcome, "reviewer": reviewer, "reviewed_at": utc_now()})
    _event(state, "MANUAL_REVIEW_RECORDED", job_id=job_id, outcome=outcome, reviewer=reviewer)
    _summarize(campaign, state)


def status_summary(state: dict[str, Any]) -> dict[str, Any]:
    return {"campaign_id": state["campaign_id"], "jobs": {s: sum(j["status"] == s for j in state["jobs"].values()) for s in STATES},
            "stages": state["stage_status"], "coverage": state["view_coverage"], "conflicts": state["conflicts"], "next_recommendation": state["next_recommendation"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="External deterministic Stage A campaign planner")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "init", "status", "next-batch", "ingest-runs", "ingest-analysis", "diagnostic", "manual-review"):
        p = sub.add_parser(name); p.add_argument("campaign")
        if name != "validate": p.add_argument("state")
        if name in {"ingest-runs", "ingest-analysis"}: p.add_argument("inputs", nargs="+")
        if name == "next-batch": p.add_argument("output")
        if name == "diagnostic": p.add_argument("action", choices=("request", "clear", "reset")); p.add_argument("job_id"); p.add_argument("--rule")
        if name == "manual-review": p.add_argument("job_id"); p.add_argument("outcome", choices=("ACCEPTED", "REJECTED")); p.add_argument("reviewer")
    args = parser.parse_args(argv); campaign = validate_campaign(load_json(args.campaign))
    if args.command == "validate": print(json.dumps({"valid": True, "fingerprint": canonical_fingerprint(campaign)})); return 0
    if args.command == "init": atomic_write(args.state, initialize_state(campaign)); return 0
    state = load_json(args.state)
    if args.command == "status": print(json.dumps(status_summary(state), indent=2, sort_keys=True)); return 0
    if args.command == "ingest-runs": ingest_manifests(campaign, state, map(load_json, args.inputs))
    elif args.command == "ingest-analysis": ingest_analysis(campaign, state, map(load_json, args.inputs))
    elif args.command == "diagnostic": diagnostic_action(campaign, state, args.job_id, args.action, args.rule)
    elif args.command == "manual-review": record_manual_review(campaign, state, args.job_id, args.outcome, args.reviewer)
    elif args.command == "next-batch":
        batch = generate_next_batch(campaign, state)
        if batch: atomic_write(args.output, batch)
        else: print(json.dumps(state["next_recommendation"]))
    atomic_write(args.state, state); return 0


if __name__ == "__main__":
    sys.exit(main())
