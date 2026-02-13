import json

import pytest

from vop_interwoven.metrics_manifest import ManifestValidationError, load_manifest_json


def test_manifest_loader_accepts_locked_v1_and_hash_is_stable(tmp_path):
    manifest_path = "vop_interwoven/metrics/manifest/metrics_manifest.v1.json"

    loaded_a = load_manifest_json(manifest_path)
    loaded_b = load_manifest_json(manifest_path)

    assert loaded_a.data["schema_version"] == "metrics_manifest.v1"
    assert loaded_a.sha256 == loaded_b.sha256
    assert loaded_a.canonical_bytes == loaded_b.canonical_bytes

    reordered_path = tmp_path / "reordered.json"
    reordered_path.write_text(
        json.dumps(loaded_a.data, indent=2, sort_keys=False), encoding="utf-8"
    )
    loaded_c = load_manifest_json(reordered_path)
    assert loaded_a.sha256 == loaded_c.sha256


def test_manifest_loader_rejects_wrong_schema_version(tmp_path):
    manifest = {
        "schema_version": "metrics_manifest.v2",
        "metrics_version": "test",
        "requires": {"primitives": [], "capabilities": []},
        "families": {"source_partition_8": {"enabled": True}},
        "invariants": [],
        "outputs": {"csv_columns": ["TotalCells"]},
    }
    path = tmp_path / "bad_schema.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="schema_version"):
        load_manifest_json(path)


def test_manifest_loader_rejects_unknown_top_level_keys(tmp_path):
    manifest = {
        "schema_version": "metrics_manifest.v1",
        "metrics_version": "test",
        "requires": {"primitives": [], "capabilities": []},
        "families": {"source_partition_8": {"enabled": True}},
        "invariants": [],
        "outputs": {"csv_columns": ["TotalCells"]},
        "extra": 123,
    }
    path = tmp_path / "unknown_key.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="Unknown top-level keys"):
        load_manifest_json(path)


def test_manifest_loader_rejects_bad_required_types(tmp_path):
    manifest = {
        "schema_version": "metrics_manifest.v1",
        "metrics_version": "test",
        "requires": {"primitives": "M", "capabilities": []},
        "families": {"source_partition_8": {"enabled": True}},
        "invariants": [],
        "outputs": {"csv_columns": ["TotalCells"]},
    }
    path = tmp_path / "bad_types.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="requires.primitives"):
        load_manifest_json(path)
