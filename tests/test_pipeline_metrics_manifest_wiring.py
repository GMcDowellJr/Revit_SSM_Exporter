import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vop_interwoven.config import Config
from vop_interwoven.pipeline import _compute_manifest_metrics_payload


def _fake_raster():
    return SimpleNamespace(
        W=2,
        H=2,
        model_mask=[False, False, False, False],
        model_proxy_mask=[False, False, False, False],
        model_edge_key=[0, -1, -1, 1],
        model_proxy_key=[-1, -1, -1, -1],
        anno_key=[-1, 0, -1, -1],
        anno_meta=[{"type": "TEXT"}],
        element_meta=[
            {"source_type": "HOST", "category": "Walls", "class": "WALL"},
            {"source_type": "DWG", "category": "Doors", "class": "DOOR"},
        ],
        has_model_present=lambda idx, mode="any": ([0, -1, -1, 1][idx] != -1),
    )


def test_compute_manifest_metrics_payload_attaches_provenance_and_validation():
    cfg = Config(metrics_validation_mode="warn")
    payload = _compute_manifest_metrics_payload(_fake_raster(), cfg)

    assert payload["metrics"]["TotalCells"] == 4
    assert payload["metrics_version"] == "2026-02-13.locked.v2"
    assert payload["metrics_manifest_file"] == "metrics_manifest.v1.json"
    assert payload["metrics_manifest_sha256"]
    assert payload["metrics_validation_mode"] == "warn"
    assert isinstance(payload["metrics_validation"], dict)
    assert payload["metrics_validation"]["manifest"]["schema_version"] == "metrics_manifest.v1"


def test_compute_manifest_metrics_payload_strict_fails_on_missing_required_primitive(tmp_path):
    manifest_path = Path("vop_interwoven/metrics/manifest/metrics_manifest.v1.json")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["requires"]["primitives"] = ["M", "MissingPrimitive"]

    bad_manifest = tmp_path / "bad_manifest.json"
    bad_manifest.write_text(json.dumps(data), encoding="utf-8")

    cfg = Config(metrics_manifest_path=str(bad_manifest), metrics_validation_mode="strict")

    with pytest.raises(Exception, match="strict"):
        _compute_manifest_metrics_payload(_fake_raster(), cfg)
