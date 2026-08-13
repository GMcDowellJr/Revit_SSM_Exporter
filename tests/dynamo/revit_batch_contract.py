"""Pure contracts for the externally planned Revit probe batch runner."""
from __future__ import absolute_import

import hashlib
import json
import os
import re


BATCH_SCHEMA_VERSION = "1.0"
MANIFEST_SCHEMA_VERSION = "1.0"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ContractError(ValueError):
    pass


def _object(value, label):
    if not isinstance(value, dict):
        raise ContractError("{0} must be an object".format(label))
    return value


def _stable_id(value, label):
    if not isinstance(value, str) or not _ID.match(value):
        raise ContractError("{0} must be a stable identifier".format(label))
    return value


def parse_view_reference(value):
    value = _object(value, "view")
    allowed = ("unique_id", "name", "view_type", "crop_active", "is_template")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ContractError("Unknown view reference fields: {0}".format(unknown))
    if not value.get("unique_id") and not value.get("name"):
        raise ContractError("view requires unique_id or name")
    if "unique_id" in value and not isinstance(value["unique_id"], str):
        raise ContractError("view.unique_id must be a string")
    if "name" in value and not isinstance(value["name"], str):
        raise ContractError("view.name must be a string")
    for key in ("crop_active", "is_template"):
        if key in value and not isinstance(value[key], bool):
            raise ContractError("view.{0} must be boolean".format(key))
    return dict(value)


def validate_batch(value):
    value = _object(value, "next_batch")
    required = ("schema_version", "campaign_id", "batch_id", "created_at",
                "document", "execution_policy", "jobs")
    missing = [key for key in required if key not in value]
    if missing:
        raise ContractError("Missing batch fields: {0}".format(missing))
    if value["schema_version"] != BATCH_SCHEMA_VERSION:
        raise ContractError("Unsupported schema_version: {0}".format(value["schema_version"]))
    unknown = sorted(set(value) - set(required))
    if unknown:
        raise ContractError("Unknown batch fields: {0}".format(unknown))
    _stable_id(value["campaign_id"], "campaign_id")
    _stable_id(value["batch_id"], "batch_id")
    document = _object(value["document"], "document")
    unknown = sorted(set(document) - {"expected_title", "expected_revit_version", "expected_path"})
    if unknown:
        raise ContractError("Unknown document fields: {0}".format(unknown))
    if not document.get("expected_title"):
        raise ContractError("document.expected_title is required")
    policy = _object(value["execution_policy"], "execution_policy")
    unknown = sorted(set(policy) - {"max_jobs_per_run", "on_job_error", "resume", "allow_rerun"})
    if unknown:
        raise ContractError("Unknown execution policy fields: {0}".format(unknown))
    if not isinstance(policy.get("max_jobs_per_run"), int) or isinstance(policy.get("max_jobs_per_run"), bool) or policy["max_jobs_per_run"] < 1:
        raise ContractError("execution_policy.max_jobs_per_run must be a positive integer")
    if policy.get("on_job_error") not in ("stop", "continue"):
        raise ContractError("execution_policy.on_job_error must be stop or continue")
    if not isinstance(policy.get("resume"), bool):
        raise ContractError("execution_policy.resume must be boolean")
    if "allow_rerun" in policy and not isinstance(policy["allow_rerun"], bool):
        raise ContractError("execution_policy.allow_rerun must be boolean")
    if not isinstance(value["jobs"], list) or not value["jobs"]:
        raise ContractError("jobs must be a non-empty array")
    seen = set()
    for index, job in enumerate(value["jobs"]):
        job = _object(job, "jobs[{0}]".format(index))
        for key in ("job_id", "probe_id", "view", "settings", "output_directory"):
            if key not in job:
                raise ContractError("jobs[{0}] is missing {1}".format(index, key))
        unknown = sorted(set(job) - {"job_id", "probe_id", "view", "settings", "output_directory"})
        if unknown:
            raise ContractError("Unknown job fields: {0}".format(unknown))
        job_id = _stable_id(job["job_id"], "job_id")
        if job_id in seen:
            raise ContractError("Duplicate job_id: {0}".format(job_id))
        seen.add(job_id)
        _stable_id(job["probe_id"], "probe_id")
        parse_view_reference(job["view"])
        _object(job["settings"], "settings")
        if not isinstance(job["output_directory"], str) or not job["output_directory"]:
            raise ContractError("output_directory must be a non-empty string")
    return value


def load_batch(path):
    with open(os.path.abspath(path), "r", encoding="utf-8") as stream:
        value = json.load(stream)
    return validate_batch(value)


def job_fingerprint(job):
    material = {key: job[key] for key in ("job_id", "probe_id", "view", "settings", "output_directory")}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_manifest(value):
    required = ("schema_version", "campaign_id", "batch_id", "run_id", "document_identity",
                "environment", "batch_source", "started_at", "completed_at", "execution_status",
                "jobs", "jobs_not_attempted")
    missing = [key for key in required if key not in value]
    if missing:
        raise ContractError("Missing manifest fields: {0}".format(missing))
    if value["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ContractError("Unsupported manifest schema_version")
    json.dumps(value, sort_keys=True)
    return True
