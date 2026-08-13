"""Thin, analysis-free execution orchestration for Dynamo/Revit."""
from __future__ import absolute_import

import json
import os
import platform
import uuid
from datetime import datetime, timezone

from tests.dynamo.revit_batch_contract import (MANIFEST_SCHEMA_VERSION, ContractError,
    job_fingerprint, load_batch, parse_view_reference, validate_batch, validate_manifest)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def view_identity(view):
    raw = getattr(view, "InternalElement", view)
    element_id = getattr(getattr(raw, "Id", None), "IntegerValue", getattr(raw, "Id", None))
    return {"unique_id": getattr(raw, "UniqueId", None), "element_id": element_id,
            "name": getattr(raw, "Name", None), "view_type": str(getattr(raw, "ViewType", None)),
            "crop_active": getattr(raw, "CropBoxActive", None), "is_template": getattr(raw, "IsTemplate", None)}


def document_identity(doc):
    return {"title": getattr(doc, "Title", None), "path": getattr(doc, "PathName", None),
            "revit_version": getattr(getattr(doc, "Application", None), "VersionNumber", None)}


def validate_document(doc, expected):
    actual = document_identity(doc)
    assertions = (("expected_title", "title"), ("expected_revit_version", "revit_version"),
                  ("expected_path", "path"))
    for requested, resolved in assertions:
        if requested in expected and str(expected[requested]) != str(actual[resolved]):
            raise ContractError("Document {0} mismatch: expected {1!r}, resolved {2!r}".format(
                resolved, expected[requested], actual[resolved]))
    return actual


def resolve_view(doc, reference, all_views=None, is_view=None):
    reference = parse_view_reference(reference)
    if reference.get("unique_id"):
        view = doc.GetElement(reference["unique_id"])
        if view is None:
            raise ContractError("No view has UniqueId {0}".format(reference["unique_id"]))
    else:
        views = list(all_views(doc) if callable(all_views) else (all_views or []))
        matches = [view for view in views if getattr(view, "Name", None) == reference["name"]]
        if len(matches) != 1:
            raise ContractError("View name {0!r} resolved to {1} views; unique resolution required".format(reference["name"], len(matches)))
        view = matches[0]
    if is_view is not None:
        valid_view = bool(is_view(view))
    elif all_views is not None:
        candidates = list(all_views(doc) if callable(all_views) else all_views)
        valid_view = any(candidate is view or getattr(candidate, "UniqueId", None) == getattr(view, "UniqueId", None)
                         for candidate in candidates)
    else:
        valid_view = all(hasattr(view, name) for name in ("UniqueId", "Name", "ViewType"))
    if not valid_view:
        raise ContractError("Resolved element is not a Revit view")
    actual = view_identity(view)
    for requested, resolved in (("unique_id", "unique_id"), ("name", "name"),
                                ("view_type", "view_type"), ("crop_active", "crop_active"),
                                ("is_template", "is_template")):
        if requested in reference and reference[requested] != actual[resolved]:
            raise ContractError("View assertion {0} mismatch: expected {1!r}, resolved {2!r}".format(requested, reference[requested], actual[resolved]))
    return view, actual


def _atomic_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp-" + uuid.uuid4().hex
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _prior_successes(root, campaign_id, batch_id):
    successes = {}
    if not os.path.isdir(root):
        return successes
    for directory, _, files in os.walk(root):
        if "revit_run_manifest.json" not in files:
            continue
        with open(os.path.join(directory, "revit_run_manifest.json"), "r", encoding="utf-8") as stream:
            prior = json.load(stream)
        if prior.get("campaign_id") != campaign_id or prior.get("batch_id") != batch_id:
            continue
        for record in prior.get("jobs", []):
            if record.get("execution_status") == "completed":
                successes[record["job_id"]] = record.get("configuration_fingerprint")
    return successes


def execute_batch(batch_or_path, doc, registry, all_views=None, manifest_root=None,
                  validation_only=False, environment=None, run_id=None, is_view=None):
    source = os.path.abspath(batch_or_path) if isinstance(batch_or_path, str) else None
    batch = load_batch(source) if source else validate_batch(batch_or_path)
    run_id = run_id or uuid.uuid4().hex
    root = os.path.abspath(manifest_root or os.path.join(os.path.dirname(source) if source else os.getcwd(), "revit_runs"))
    path = os.path.join(root, run_id, "revit_run_manifest.json")
    started = utc_now()
    manifest = {"schema_version": MANIFEST_SCHEMA_VERSION, "campaign_id": batch["campaign_id"],
        "batch_id": batch["batch_id"], "run_id": run_id, "document_identity": {},
        "environment": environment or {"python": platform.python_version()},
        "batch_source": source or "in-memory", "started_at": started, "completed_at": None,
        "execution_status": "running", "validation_only": bool(validation_only), "jobs": [],
        "jobs_not_attempted": [], "errors": [], "warnings": []}
    try:
        manifest["document_identity"] = validate_document(doc, batch["document"])
        resolved = []
        for job in batch["jobs"]:
            if job["probe_id"] not in registry:
                raise ContractError("Unknown probe_id: {0}".format(job["probe_id"]))
            adapter_validator = getattr(registry[job["probe_id"]], "validate_settings", None)
            if adapter_validator is not None:
                adapter_validator(dict(job["settings"]), job["output_directory"])
            view, identity = resolve_view(doc, job["view"], all_views, is_view)
            resolved.append((job, view, identity))
        policy = batch["execution_policy"]
        prior = _prior_successes(root, batch["campaign_id"], batch["batch_id"]) if policy["resume"] else {}
        selected = []
        for job, view, identity in resolved:
            fingerprint = job_fingerprint(job)
            if job["job_id"] in prior and not policy.get("allow_rerun", False):
                if prior[job["job_id"]] != fingerprint:
                    raise ContractError("Configuration drift for completed job {0}".format(job["job_id"]))
                manifest["jobs"].append(_job_record(job, identity, fingerprint, "skipped_resume"))
            else:
                selected.append((job, view, identity, fingerprint))
        limit = policy["max_jobs_per_run"]
        attempted, deferred = selected[:limit], selected[limit:]
        manifest["jobs_not_attempted"] = [{"job_id": item[0]["job_id"], "reason": "execution_limit"} for item in deferred]
        if validation_only:
            for job, _, identity, fingerprint in attempted + deferred:
                manifest["jobs"].append(_job_record(job, identity, fingerprint, "validated_only"))
        else:
            _atomic_json(path, manifest)
            for index, (job, view, identity, fingerprint) in enumerate(attempted):
                record = _job_record(job, identity, fingerprint, "running")
                manifest["jobs"].append(record)
                _atomic_json(path, manifest)
                record["started_at"] = utc_now()
                try:
                    envelope = registry[job["probe_id"]](view, dict(job["settings"]), job["output_directory"])
                    record.update({"raw_result_envelope": envelope, "artifact_paths": envelope.get("artifact_paths", []),
                        "rollback_status": envelope.get("rollback_status", "unknown"),
                        "state_restoration_status": envelope.get("state_restoration_status", "unknown"),
                        "errors": envelope.get("errors", []), "warnings": envelope.get("warnings", []),
                        "execution_status": envelope.get("execution_status", "failed")})
                    if record["execution_status"] == "failed" and policy["on_job_error"] == "stop":
                        manifest["jobs_not_attempted"].extend({"job_id": later[0]["job_id"], "reason": "stopped_after_job_error"} for later in attempted[index + 1:])
                        record["completed_at"] = utc_now()
                        _atomic_json(path, manifest)
                        break
                except Exception as error:
                    record["execution_status"] = "failed"
                    record["errors"] = [{"type": type(error).__name__, "message": str(error)}]
                    if policy["on_job_error"] == "stop":
                        manifest["jobs_not_attempted"].extend({"job_id": later[0]["job_id"], "reason": "stopped_after_job_error"} for later in attempted[index + 1:])
                        record["completed_at"] = utc_now()
                        _atomic_json(path, manifest)
                        break
                record["completed_at"] = utc_now()
                _atomic_json(path, manifest)
        manifest["execution_status"] = "validation_only" if validation_only else ("failed" if any(j["execution_status"] == "failed" for j in manifest["jobs"]) else "completed")
    except Exception as error:
        manifest["execution_status"] = "configuration_failed"
        manifest["errors"].append({"type": type(error).__name__, "message": str(error)})
    manifest["completed_at"] = utc_now()
    validate_manifest(manifest)
    _atomic_json(path, manifest)
    executed = [j["job_id"] for j in manifest["jobs"] if j["execution_status"] in ("completed", "failed", "inconclusive")]
    failed = [j["job_id"] for j in manifest["jobs"] if j["execution_status"] == "failed"]
    return {"campaign_id": batch["campaign_id"], "batch_id": batch["batch_id"], "run_id": run_id,
            "validation_only": bool(validation_only), "jobs_executed": executed, "jobs_failed": failed,
            "jobs_deferred": [j["job_id"] for j in manifest["jobs_not_attempted"] if j["reason"] == "execution_limit"],
            "manifest_path": path, "another_invocation_needed": bool(manifest["jobs_not_attempted"])}


def _job_record(job, resolved_identity, fingerprint, status):
    return {"job_id": job["job_id"], "probe_id": job["probe_id"],
        "requested_view_identity": dict(job["view"]), "resolved_view_identity": resolved_identity,
        "requested_settings": dict(job["settings"]), "configuration_fingerprint": fingerprint,
        "execution_status": status, "raw_result_envelope": None, "artifact_paths": [],
        "rollback_status": "not_started", "state_restoration_status": "not_checked",
        "errors": [], "warnings": [], "started_at": None, "completed_at": None}
