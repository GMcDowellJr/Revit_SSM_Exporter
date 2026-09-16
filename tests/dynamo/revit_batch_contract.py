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
    """Validate one job's view reference.

    ``element_id`` exists alongside ``unique_id`` and ``name`` because those
    two are not always usable in practice. A UniqueId has to be looked up by
    hand, and a name is not necessarily unique -- a model can hold two views
    both called "SEA LEVEL", which resolve_view correctly refuses rather than
    guessing between. The integer ElementId is the identifier a person
    actually has in front of them (it is what the Stage A outputs are named
    by), and it is unambiguous, so a campaign can be written without a manual
    lookup step that is itself a source of error.

    ``name`` may still be given alongside it, in which case resolve_view
    asserts the two agree -- an id pointing at a renamed or different view
    fails loudly instead of silently probing the wrong one.
    """
    value = _object(value, "view")
    allowed = ("unique_id", "element_id", "name", "view_type", "crop_active", "is_template")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ContractError("Unknown view reference fields: {0}".format(unknown))
    if not value.get("unique_id") and not value.get("name") and value.get("element_id") is None:
        raise ContractError("view requires unique_id, element_id or name")
    if "unique_id" in value and not isinstance(value["unique_id"], str):
        raise ContractError("view.unique_id must be a string")
    if "element_id" in value and (isinstance(value["element_id"], bool)
                                  or not isinstance(value["element_id"], int)):
        raise ContractError("view.element_id must be an integer")
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
    unknown = sorted(set(value) - set(required) - {"campaign_configuration_fingerprint"})
    if unknown:
        raise ContractError("Unknown batch fields: {0}".format(unknown))
    _stable_id(value["campaign_id"], "campaign_id")
    _stable_id(value["batch_id"], "batch_id")
    fingerprint = value.get("campaign_configuration_fingerprint")
    if fingerprint is not None and (not isinstance(fingerprint, str) or not re.match(r"^[a-f0-9]{64}$", fingerprint)):
        raise ContractError("campaign_configuration_fingerprint must be a SHA-256 hex digest")
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
    # `variant` and `job_configuration_fingerprint` are optional batch-job-level
    # metadata (never probe-facing settings, never part of dispatch identity -
    # see job_fingerprint(), which deliberately excludes them).
    known_job_fields = {"job_id", "probe_id", "view", "settings", "output_directory",
                        "variant", "job_configuration_fingerprint"}
    for index, job in enumerate(value["jobs"]):
        job = _object(job, "jobs[{0}]".format(index))
        for key in ("job_id", "probe_id", "view", "settings", "output_directory"):
            if key not in job:
                raise ContractError("jobs[{0}] is missing {1}".format(index, key))
        unknown = sorted(set(job) - known_job_fields)
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
        if "variant" in job and not isinstance(job["variant"], str):
            raise ContractError("job.variant must be a string")
    return value


def load_batch(path):
    with open(os.path.abspath(path), "r", encoding="utf-8") as stream:
        value = json.load(stream)
    return validate_batch(value)


def job_fingerprint(job):
    # `variant` is dispatch identity, not incidental metadata: the specialized
    # stage_a_minimum_id_mutations adapter maps it to run_probe(selection=...)
    # whenever `settings` doesn't already carry an explicit `selection`. It
    # must be part of the fingerprint the planner's execution_fingerprints and
    # this executor's ingestion/resume checks agree on, or a batch job could
    # be tampered with to dispatch a different variant while still matching
    # the expected fingerprint.
    material = {key: job.get(key) for key in ("job_id", "probe_id", "view", "settings", "output_directory", "variant")}
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
