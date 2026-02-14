"""Metrics manifest loading and validation utilities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_TOP_LEVEL_KEYS = {
    "schema_version",
    "metrics_version",
    "requires",
    "families",
    "invariants",
    "outputs",
}


class ManifestValidationError(ValueError):
    """Raised when a manifest is invalid."""


@dataclass(frozen=True)
class MetricsManifest:
    """Validated manifest plus canonical provenance metadata."""

    data: dict[str, Any]
    canonical_bytes: bytes
    sha256: str
    file_name: str


class ManifestLoader:
    """Loads and validates metrics manifests."""

    @staticmethod
    def load(path: str | Path) -> MetricsManifest:
        return load_manifest_json(path)


def _ensure_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{path} must be an object")
    return value


def _ensure_string(value: Any, path: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ManifestValidationError(f"{path} must be a string")
    if not allow_empty and not value.strip():
        raise ManifestValidationError(f"{path} must be non-empty")
    return value


def _ensure_string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ManifestValidationError(f"{path} must be a list of strings")
    if len(set(value)) != len(value):
        raise ManifestValidationError(f"{path} must contain unique values")
    return value


def validate_manifest_schema_version(data: dict[str, Any]) -> None:
    schema = _ensure_string(data.get("schema_version"), "schema_version")
    if schema != "metrics_manifest.v1":
        raise ManifestValidationError("schema_version must equal 'metrics_manifest.v1'")


def validate_manifest_top_level_keys(data: dict[str, Any]) -> None:
    unknown = set(data.keys()) - ALLOWED_TOP_LEVEL_KEYS
    missing = ALLOWED_TOP_LEVEL_KEYS - set(data.keys())
    if unknown:
        raise ManifestValidationError(f"Unknown top-level keys: {sorted(unknown)}")
    if missing:
        raise ManifestValidationError(f"Missing top-level keys: {sorted(missing)}")


def validate_requires_block(data: dict[str, Any]) -> None:
    requires = _ensure_object(data.get("requires"), "requires")
    unknown = set(requires.keys()) - {"primitives", "capabilities"}
    if unknown:
        raise ManifestValidationError(f"Unknown requires keys: {sorted(unknown)}")

    _ensure_string_list(requires.get("primitives"), "requires.primitives")
    _ensure_string_list(requires.get("capabilities"), "requires.capabilities")


def validate_families_block(data: dict[str, Any]) -> None:
    families = _ensure_object(data.get("families"), "families")
    if not families:
        raise ManifestValidationError("families must be non-empty")

    for family_name, family_value in families.items():
        family_obj = _ensure_object(family_value, f"families.{family_name}")
        if "enabled" not in family_obj:
            raise ManifestValidationError(f"families.{family_name}.enabled is required")
        if not isinstance(family_obj["enabled"], bool):
            raise ManifestValidationError(f"families.{family_name}.enabled must be a boolean")


def validate_invariants_block(data: dict[str, Any]) -> None:
    invariants = data.get("invariants")
    if not isinstance(invariants, list):
        raise ManifestValidationError("invariants must be a list")

    seen_ids: set[str] = set()
    for idx, invariant in enumerate(invariants):
        inv = _ensure_object(invariant, f"invariants[{idx}]")
        inv_id = _ensure_string(inv.get("id"), f"invariants[{idx}].id")
        level = _ensure_string(inv.get("level"), f"invariants[{idx}].level")
        if inv_id in seen_ids:
            raise ManifestValidationError(f"Duplicate invariant id: {inv_id}")
        seen_ids.add(inv_id)

        if level not in {"error", "warn"}:
            raise ManifestValidationError(
                f"invariants[{idx}].level must be one of ['error', 'warn']"
            )

        expr = inv.get("expr")
        if not isinstance(expr, dict):
            raise ManifestValidationError(f"invariants[{idx}].expr must be an object")


def validate_outputs_block(data: dict[str, Any]) -> None:
    outputs = _ensure_object(data.get("outputs"), "outputs")
    unknown = set(outputs.keys()) - {"csv_columns"}
    if unknown:
        raise ManifestValidationError(f"Unknown outputs keys: {sorted(unknown)}")

    csv_columns = _ensure_string_list(outputs.get("csv_columns"), "outputs.csv_columns")
    if not csv_columns:
        raise ManifestValidationError("outputs.csv_columns must be non-empty")


def canonicalize_manifest_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def compute_manifest_sha256(canonical_bytes: bytes) -> str:
    return hashlib.sha256(canonical_bytes).hexdigest()


def load_manifest_json(path: str | Path) -> MetricsManifest:
    path_obj = Path(path)
    try:
        raw_data = json.loads(path_obj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(f"Invalid JSON: {exc}") from exc

    data = _ensure_object(raw_data, "root")
    validate_manifest_top_level_keys(data)
    validate_manifest_schema_version(data)
    _ensure_string(data.get("metrics_version"), "metrics_version")
    validate_requires_block(data)
    validate_families_block(data)
    validate_invariants_block(data)
    validate_outputs_block(data)

    canonical_bytes = canonicalize_manifest_json(data)
    sha256 = compute_manifest_sha256(canonical_bytes)

    return MetricsManifest(
        data=data,
        canonical_bytes=canonical_bytes,
        sha256=sha256,
        file_name=path_obj.name,
    )
