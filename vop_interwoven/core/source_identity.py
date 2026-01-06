"""Source identity normalization for VOP interwoven.

Purpose
- Establish a single explicit schema for source identity.
- Prevent doc_key parsing in performance-critical loops.

Schema
- source_type: one of {"HOST", "LINK", "DWG"}
- source_id: stable, unique string identifier for the source instance
- source_label: human-readable label (may be non-unique)

Notes
- This is intentionally "dataclass-like" but kept minimal for Dynamo safety.
"""

SOURCE_HOST = "HOST"
SOURCE_LINK = "LINK"
SOURCE_DWG  = "DWG"

_ALLOWED = {SOURCE_HOST, SOURCE_LINK, SOURCE_DWG}


def make_source_identity(source_type, source_id, source_label=None):
    """Return a normalized source identity dict.

    This is the only place that should validate source semantics.

    Args:
        source_type: "HOST" | "LINK" | "DWG"
        source_id: stable unique string (required)
        source_label: optional human label (defaults to source_id)

    Returns:
        dict with keys: source_type, source_id, source_label

    Raises:
        ValueError if inputs are invalid.
    """
    if source_type not in _ALLOWED:
        raise ValueError("Invalid source_type: {0}".format(source_type))
    if source_id is None:
        raise ValueError("source_id is required")
    sid = str(source_id)
    if not sid:
        raise ValueError("source_id must be a non-empty string")
    return {
        "source_type": source_type,
        "source_id": sid,
        "source_label": str(source_label) if source_label is not None else sid,
    }
