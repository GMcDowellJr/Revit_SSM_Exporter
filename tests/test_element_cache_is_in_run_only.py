"""The element cache's cross-run role is retired (Greg, 2026-09-29).

A run neither loads a previous run's vop_element_cache_<date>.json nor
writes one, and never runs detect_changes / writes element_changes.csv:
change detection belongs to the analysis layer, from sidecar bbox_3d +
bbox_transform. The IN-RUN cache is untouched.
"""
import os

import pytest

from vop_interwoven.core.element_cache import ElementCache
from tests import test_stage_a_skips_the_model_pass as loop


@pytest.mark.parametrize("stage_a", [True, False])
def test_a_run_neither_loads_nor_writes_the_cross_run_cache(monkeypatch, tmp_path, stage_a):
    # RECORDED, not raised: production wraps these calls in try/except, so a
    # raising stub would be swallowed and this test would prove nothing
    # (it did, until proven against the pre-retirement code).
    calls = []

    def _record(name):
        def _stub(*a, **k):
            calls.append(name)
            return None
        return _stub
    monkeypatch.setattr(ElementCache, "load_from_json", classmethod(
        lambda cls, *a, **k: calls.append("load_from_json")))
    monkeypatch.setattr(ElementCache, "save_to_json", _record("save_to_json"))
    monkeypatch.setattr(ElementCache, "detect_changes", _record("detect_changes"))

    _model, _capture, results = loop._run_one_view(monkeypatch, tmp_path, stage_a=stage_a)
    assert len(results) == 1
    assert calls == [], calls
    names = os.listdir(str(tmp_path))
    assert not any(n.startswith("vop_element_cache_") for n in names), names
    assert "element_changes.csv" not in names
