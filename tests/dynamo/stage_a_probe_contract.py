"""Pure helpers shared by the Stage A Dynamo probes.

This module deliberately has no Dynamo or Revit imports.  A future executor can
therefore validate requests and results before entering a Revit API boundary.
"""
from __future__ import absolute_import

from datetime import datetime, timezone


PROBE_SCHEMA_VERSION = "1.0"
EXECUTION_STATUSES = ("completed", "failed", "inconclusive")
ROLLBACK_STATUSES = ("succeeded", "failed", "not_started", "unknown")
RESTORATION_STATUSES = ("restored", "not_restored", "not_checked", "unknown")


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def select_named(selection, supported, label="selection"):
    """Return a stable, de-duplicated requested subset or every default item."""
    available = list(supported)
    if selection is None or (isinstance(selection, str) and selection.strip().lower() == "all"):
        return available
    if isinstance(selection, str):
        requested = [part.strip() for part in selection.replace(";", ",").split(",") if part.strip()]
    else:
        requested = [str(part).strip() for part in selection if str(part).strip()]
    unknown = [name for name in requested if name not in available]
    if unknown:
        raise ValueError("Unknown {0}: {1}. Supported values: {2}".format(label, unknown, available))
    result = []
    for name in requested:
        if name not in result:
            result.append(name)
    return result


def view_identity(view):
    view = getattr(view, "InternalElement", view)
    element_id = getattr(view, "Id", None)
    integer_id = getattr(element_id, "IntegerValue", element_id)
    try:
        integer_id = int(integer_id)
    except (TypeError, ValueError):
        integer_id = None
    return {"id": integer_id, "name": getattr(view, "Name", None), "type": str(getattr(view, "ViewType", None))}


def execution_envelope(probe_id, requested_settings, resolved_view_identity,
                       native_report, artifact_paths, rollback_status,
                       state_restoration_status, started_at, completed_at=None,
                       execution_status="completed", errors=None, warnings=None):
    envelope = {
        "probe_id": str(probe_id),
        "probe_schema_version": PROBE_SCHEMA_VERSION,
        "requested_settings": requested_settings or {},
        "resolved_view_identity": resolved_view_identity or {},
        "execution_status": execution_status,
        "native_report": native_report,
        "artifact_paths": list(artifact_paths or []),
        "rollback_status": rollback_status,
        "state_restoration_status": state_restoration_status,
        "errors": list(errors or []),
        "warnings": list(warnings or []),
        "started_at": started_at,
        "completed_at": completed_at or utc_now_iso(),
    }
    validate_execution_envelope(envelope)
    return envelope


def validate_execution_envelope(value):
    required = ("probe_id", "probe_schema_version", "requested_settings",
                "resolved_view_identity", "execution_status", "native_report",
                "artifact_paths", "rollback_status", "state_restoration_status",
                "errors", "warnings", "started_at", "completed_at")
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError("Execution envelope is missing fields: {0}".format(missing))
    if value["execution_status"] not in EXECUTION_STATUSES:
        raise ValueError("Unknown execution_status: {0}".format(value["execution_status"]))
    if value["rollback_status"] not in ROLLBACK_STATUSES:
        raise ValueError("Unknown rollback_status: {0}".format(value["rollback_status"]))
    if value["state_restoration_status"] not in RESTORATION_STATUSES:
        raise ValueError("Unknown state_restoration_status: {0}".format(value["state_restoration_status"]))
    # TIFF-derived judgments belong to an external analyzer, never this envelope.
    forbidden = ("image_fidelity_status", "alignment_status", "semantic_preservation_status", "campaign_acceptance")
    present = [key for key in forbidden if key in value]
    if present:
        raise ValueError("Image-analysis fields are not execution-envelope fields: {0}".format(present))
    return True
