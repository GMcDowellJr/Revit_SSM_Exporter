"""Thin, analysis-free execution orchestration for Dynamo/Revit."""
from __future__ import absolute_import

import inspect
import json
import os
import platform
import uuid
from datetime import datetime, timezone

from tests.dynamo.revit_batch_contract import (MANIFEST_SCHEMA_VERSION, ContractError,
    job_fingerprint, load_batch, parse_view_reference, validate_batch, validate_manifest)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _view_type_name(raw):
    """Return a stable Revit ViewType member name, never a bare ordinal.

    Some Dynamo/IronPython interop paths stringify ``View.ViewType`` as a
    number (e.g. ``"1"``) instead of its enum member name (e.g.
    ``"FloorPlan"``). Comparing/recording that raw ordinal is the historical
    ViewType-as-integer defect: view_type assertions in resolve_view() and
    manifest identity must always see the stable name.
    """
    value = getattr(raw, "ViewType", None)
    if value is None:
        return None
    text = str(value)
    if not text.lstrip("-").isdigit():
        return text  # already a stable member name
    try:
        from Autodesk.Revit.DB import ViewType as _ViewType  # noqa: local import, Revit-only
        for name in dir(_ViewType):
            if name.startswith("_"):
                continue
            member = getattr(_ViewType, name, None)
            try:
                if member is not None and str(int(member)) == text:
                    return name
            except (TypeError, ValueError):
                continue
    except Exception:
        pass
    return text  # last resort: never crash, but this remains an unresolved ordinal


def view_identity(view):
    raw = getattr(view, "InternalElement", view)
    element_id = getattr(getattr(raw, "Id", None), "IntegerValue", getattr(raw, "Id", None))
    return {"unique_id": getattr(raw, "UniqueId", None), "element_id": element_id,
            "name": getattr(raw, "Name", None), "view_type": _view_type_name(raw),
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
    elif reference.get("element_id") is not None:
        from Autodesk.Revit.DB import ElementId
        view = doc.GetElement(ElementId(int(reference["element_id"])))
        if view is None:
            raise ContractError("No view has ElementId {0}".format(reference["element_id"]))
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
        # element_id is deliberately absent: it is how the view was found, so
        # asserting it against itself proves nothing. Any `name` given beside
        # it IS asserted here, which is what catches an id that has drifted
        # onto a different or renamed view.
        if requested in reference and reference[requested] != actual[resolved]:
            raise ContractError("View assertion {0} mismatch: expected {1!r}, resolved {2!r}".format(requested, reference[requested], actual[resolved]))
    return view, actual


def resolve_output_directory(output_directory, batch_source_path=None, artifact_root=None):
    """Resolve a job's configured ``output_directory`` to a deterministic
    absolute path, independent of the Revit/Dynamo process's current working
    directory at execution time.

    Resolution order: an already-absolute path is used as-is; otherwise it is
    resolved against an explicit ``artifact_root`` when one is configured, and
    otherwise against the directory containing ``next_batch.json`` (a location
    that is always known and stable, unlike the process cwd).
    """
    if output_directory is None:
        raise ContractError("job output_directory is required")
    if os.path.isabs(output_directory):
        return os.path.normpath(output_directory)
    # `artifact_root` itself must be made absolute: a caller-supplied relative
    # artifact_root would otherwise still leave the final path relative,
    # silently depending on the process cwd again at the point it's later
    # opened - defeating the whole point of this function.
    base = os.path.abspath(artifact_root) if artifact_root else (os.path.dirname(os.path.abspath(batch_source_path)) if batch_source_path else os.getcwd())
    return os.path.normpath(os.path.join(base, output_directory))


def _variant_kwargs(adapter, job):
    """Pass job["variant"] to adapters that declare a `variant` parameter
    (the explicit per-probe adapter boundary); older/generic adapters that
    don't accept it are called exactly as before."""
    try:
        accepts = "variant" in inspect.signature(adapter).parameters
    except (TypeError, ValueError):
        accepts = False
    return {"variant": job.get("variant")} if accepts else {}


def _atomic_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp-" + uuid.uuid4().hex
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _prior_successes(root, campaign_id, batch_id, current_document_identity):
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
        completed = [record for record in prior.get("jobs", [])
                     if record.get("execution_status") == "completed"]
        if not completed:
            continue
        if prior.get("document_identity") != current_document_identity:
            raise ContractError("Cannot resume campaign {0} batch {1}: prior run {2} belongs to a different document".format(
                campaign_id, batch_id, prior.get("run_id", "unknown")))
        for record in completed:
            successes[record["job_id"]] = record.get("configuration_fingerprint")
    return successes


def execute_batch(batch_or_path, doc, registry, all_views=None, manifest_root=None,
                  validation_only=False, environment=None, run_id=None, is_view=None,
                  artifact_root=None):
    source = os.path.abspath(batch_or_path) if isinstance(batch_or_path, str) else None
    run_id = run_id or uuid.uuid4().hex
    root = os.path.abspath(manifest_root or os.path.join(os.path.dirname(source) if source else os.getcwd(), "revit_runs"))
    path = os.path.join(root, run_id, "revit_run_manifest.json")
    started = utc_now()
    supplied = batch_or_path if isinstance(batch_or_path, dict) else {}
    manifest = {"schema_version": MANIFEST_SCHEMA_VERSION,
        "campaign_id": supplied.get("campaign_id"), "batch_id": supplied.get("batch_id"),
        "run_id": run_id, "document_identity": document_identity(doc),
        "environment": environment or {"python": platform.python_version()},
        "batch_source": source or "in-memory", "started_at": started, "completed_at": None,
        "execution_status": "running", "validation_only": bool(validation_only), "jobs": [],
        "jobs_not_attempted": [], "errors": [], "warnings": []}
    try:
        batch = load_batch(source) if source else validate_batch(batch_or_path)
        manifest["campaign_id"] = batch["campaign_id"]
        manifest["batch_id"] = batch["batch_id"]
        manifest["document_identity"] = validate_document(doc, batch["document"])
        resolved = []
        for job in batch["jobs"]:
            if job["probe_id"] not in registry:
                raise ContractError("Unknown probe_id: {0}".format(job["probe_id"]))
            resolved_output_directory = resolve_output_directory(job["output_directory"], source, artifact_root)
            adapter_validator = getattr(registry[job["probe_id"]], "validate_settings", None)
            if adapter_validator is not None:
                adapter_validator(dict(job["settings"]), resolved_output_directory, **_variant_kwargs(adapter_validator, job))
            view, identity = resolve_view(doc, job["view"], all_views, is_view)
            resolved.append((job, view, identity, resolved_output_directory))
        policy = batch["execution_policy"]
        prior = _prior_successes(root, batch["campaign_id"], batch["batch_id"],
                                 manifest["document_identity"]) if policy["resume"] else {}
        selected = []
        for job, view, identity, resolved_output_directory in resolved:
            fingerprint = job_fingerprint(job)
            if job["job_id"] in prior and not policy.get("allow_rerun", False):
                if prior[job["job_id"]] != fingerprint:
                    raise ContractError("Configuration drift for completed job {0}".format(job["job_id"]))
                manifest["jobs"].append(_job_record(job, identity, fingerprint, "skipped_resume", resolved_output_directory))
            else:
                selected.append((job, view, identity, fingerprint, resolved_output_directory))
        limit = policy["max_jobs_per_run"]
        attempted, deferred = selected[:limit], selected[limit:]
        manifest["jobs_not_attempted"] = [{"job_id": item[0]["job_id"], "reason": "execution_limit"} for item in deferred]
        if validation_only:
            for job, _, identity, fingerprint, resolved_output_directory in attempted + deferred:
                manifest["jobs"].append(_job_record(job, identity, fingerprint, "validated_only", resolved_output_directory))
        else:
            _atomic_json(path, manifest)
            for index, (job, view, identity, fingerprint, resolved_output_directory) in enumerate(attempted):
                record = _job_record(job, identity, fingerprint, "running", resolved_output_directory)
                manifest["jobs"].append(record)
                _atomic_json(path, manifest)
                record["started_at"] = utc_now()
                try:
                    adapter = registry[job["probe_id"]]
                    envelope = adapter(view, dict(job["settings"]), resolved_output_directory, **_variant_kwargs(adapter, job))
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
        manifest["errors"].append({"phase": "configuration", "type": type(error).__name__,
                                   "message": str(error)})
    manifest["completed_at"] = utc_now()
    validate_manifest(manifest)
    _atomic_json(path, manifest)
    executed = [j["job_id"] for j in manifest["jobs"] if j["execution_status"] in ("completed", "failed", "inconclusive")]
    failed = [j["job_id"] for j in manifest["jobs"] if j["execution_status"] == "failed"]
    # execution_status and errors are part of the summary, not just the
    # manifest. A run that fails in the configuration phase -- a document title
    # that does not match, a view that will not resolve, a setting a probe
    # rejects -- never populates any of the lists below, so without these two
    # fields it returns empty lists and another_invocation_needed=false, which
    # is indistinguishable from a clean run with nothing left to do. That is
    # precisely the case a validation-only run exists to surface, and it was
    # the one the summary hid.
    summary = {"campaign_id": manifest["campaign_id"], "batch_id": manifest["batch_id"], "run_id": run_id,
            "validation_only": bool(validation_only), "execution_status": manifest["execution_status"],
            "jobs_validated": [j["job_id"] for j in manifest["jobs"] if j["execution_status"] == "validated_only"],
            "jobs_executed": executed, "jobs_failed": failed,
            "jobs_deferred": [j["job_id"] for j in manifest["jobs_not_attempted"] if j["reason"] == "execution_limit"],
            "errors": list(manifest["errors"]),
            "manifest_path": path, "another_invocation_needed": bool(manifest["jobs_not_attempted"])}
    if manifest["execution_status"] == "configuration_failed":
        # Say it in a field a person reads first, not only in an errors array
        # they have to notice is non-empty.
        summary["configuration_error"] = (manifest["errors"][0]["message"]
                                          if manifest["errors"] else "unknown configuration error")
        summary["another_invocation_needed"] = False
    return summary


def _job_record(job, resolved_identity, fingerprint, status, resolved_output_directory=None):
    return {"job_id": job["job_id"], "probe_id": job["probe_id"],
        "requested_view_identity": dict(job["view"]), "resolved_view_identity": resolved_identity,
        "requested_settings": dict(job["settings"]), "configuration_fingerprint": fingerprint,
        # `output_directory` preserves the configured (often relative) identity
        # exactly as authored; `output_directory_resolved` is the canonical
        # absolute path actually used for dispatch/artifacts, independent of
        # the Revit/Dynamo process's current working directory.
        "output_directory": job.get("output_directory"),
        "output_directory_resolved": resolved_output_directory,
        "execution_status": status, "raw_result_envelope": None, "artifact_paths": [],
        "rollback_status": "not_started", "state_restoration_status": "not_checked",
        "errors": [], "warnings": [], "started_at": None, "completed_at": None}
