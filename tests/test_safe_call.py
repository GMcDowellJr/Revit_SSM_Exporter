# tests/test_safe_call.py

import pytest

from vop_interwoven.core.diagnostics import Diagnostics
from vop_interwoven.revit.safe_api import safe_call


def test_safe_call_default_records_and_returns_default():
    diag = Diagnostics(max_events=10)

    def boom():
        raise ValueError("x")

    result = safe_call(
        diag,
        phase="unit",
        callsite="safe_call_default",
        fn=boom,
        default=42,
    )

    assert result == 42
    d = diag.to_dict()
    assert d["num_events"] == 1
    assert d["events"][0]["exc_type"] == "ValueError"


def test_safe_call_raise_records_and_raises():
    diag = Diagnostics(max_events=10)

    def boom():
        raise RuntimeError("y")

    with pytest.raises(RuntimeError):
        safe_call(
            diag,
            phase="unit",
            callsite="safe_call_raise",
            fn=boom,
            default=None,
            policy="raise",
        )

    d = diag.to_dict()
    assert d["num_events"] == 1
    assert d["events"][0]["exc_type"] == "RuntimeError"
