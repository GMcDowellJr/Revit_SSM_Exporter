# tests/test_diagnostics.py

import json
import pytest

from vop_interwoven.core.diagnostics import Diagnostics


def test_error_records_event_and_counts():
    diag = Diagnostics(max_events=10, capture_traceback=False)

    try:
        raise ValueError("boom")
    except Exception as e:
        diag.error(
            phase="unit",
            callsite="test_error_records_event_and_counts",
            message="failed",
            exc=e,
            view_id=123,
            elem_id=456,
            source="HOST",
            doc_key="docA",
            extra={"k": "v"},
        )

    d = diag.to_dict()
    assert d["num_events"] == 1
    assert d["dropped_events"] == 0
    assert isinstance(d["counts"], dict)
    assert isinstance(d["events"], list)
    assert d["events"][0]["level"] == "ERROR"
    assert d["events"][0]["phase"] == "unit"
    assert d["events"][0]["callsite"] == "test_error_records_event_and_counts"
    assert d["events"][0]["exc_type"] == "ValueError"
    assert "boom" in (d["events"][0]["exc_message"] or "")


def test_event_cap_drops_but_counts_continue():
    diag = Diagnostics(max_events=2, capture_traceback=False)

    for i in range(7):
        try:
            raise RuntimeError(f"r{i}")
        except Exception as e:
            diag.error(
                phase="unit",
                callsite="cap",
                message="err",
                exc=e,
            )

    d = diag.to_dict()
    assert d["num_events"] == 2
    assert d["dropped_events"] == 5

    # counts must reflect all 7 errors, not just the 2 stored events
    total = sum(d["counts"].values())
    assert total == 7


def test_to_dict_is_json_serializable():
    diag = Diagnostics(max_events=3, capture_traceback=False)

    try:
        raise Exception("x")
    except Exception as e:
        diag.error(phase="unit", callsite="json", message="m", exc=e)

    payload = diag.to_dict()
    # Must not throw:
    json.dumps(payload)

def test_debug_dedupe_records_once_and_updates_suppressed_count():
    diag = Diagnostics(max_events=10, capture_traceback=False)

    for i in range(5):
        diag.debug_dedupe(
            dedupe_key="k",
            phase="unit",
            callsite="dedupe",
            message="m",
            view_id=1,
            elem_id=100 + i,
            extra={"x": "y"},
        )

    d = diag.to_dict()
    assert d["num_events"] == 1
    ev = d["events"][0]
    assert ev["level"] == "DEBUG"
    assert ev["extra"]["suppressed_count"] == 4
