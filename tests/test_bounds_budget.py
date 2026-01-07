import pytest

from vop_interwoven.core.math_utils import Bounds2D
from vop_interwoven.core.diagnostics import Diagnostics
from vop_interwoven.revit.view_basis import resolve_view_bounds


class _StubView(object):
    def __init__(self, crop_active=False):
        self.CropBoxActive = bool(crop_active)
        self.Scale = 96
        self.Name = "StubView"

        class _Id(object):
            IntegerValue = 456

        self.Id = _Id()


def test_bounds_budget_trigger_downgrades_confidence_to_low_and_reports_budget():
    view = _StubView(crop_active=False)
    diag = Diagnostics()

    def extents_fn(_view, _policy):
        return {
            "bounds_uv": Bounds2D(0, 0, 10, 10),
            "confidence": "low",
            "budget": {"triggered": True, "reason": "max_elements", "scanned": 123, "found": 45},
        }

    r = resolve_view_bounds(
        view,
        diag=diag,
        policy={
            "cell_size_ft": 1.0,
            "buffer_ft": 0.0,
            "max_W": 999,
            "max_H": 999,
            "bounds_extents_fn": extents_fn,
        },
    )

    assert r["reason"] == "extents"
    assert r["confidence"] == "low"
    assert r["bounds_budget"]["triggered"] is True
    assert r["bounds_budget"]["reason"] == "max_elements"
    assert _as_tuple(r["bounds_uv"]) == (0.0, 0.0, 10.0, 10.0)


def test_bounds_budget_absent_keeps_med_confidence_for_extents():
    view = _StubView(crop_active=False)
    diag = Diagnostics()

    def extents_fn(_view, _policy):
        return {
            "bounds_uv": Bounds2D(0, 0, 10, 10),
            "confidence": "med",
            "budget": {"triggered": False, "reason": None},
        }

    r = resolve_view_bounds(
        view,
        diag=diag,
        policy={
            "cell_size_ft": 1.0,
            "buffer_ft": 0.0,
            "max_W": 999,
            "max_H": 999,
            "bounds_extents_fn": extents_fn,
        },
    )

    assert r["reason"] == "extents"
    assert r["confidence"] == "med"
    assert r["bounds_budget"]["triggered"] is False


def _as_tuple(b):
    return (float(b.xmin), float(b.ymin), float(b.xmax), float(b.ymax))
