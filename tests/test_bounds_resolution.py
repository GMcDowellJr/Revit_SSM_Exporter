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
            IntegerValue = 123

        self.Id = _Id()


def test_resolve_bounds_crop_on_uses_crop_reason_and_high_confidence():
    view = _StubView(crop_active=True)
    diag = Diagnostics()

    def crop_fn(_view, _policy):
        return Bounds2D(0, 0, 10, 20)

    r = resolve_view_bounds(
        view,
        diag=diag,
        policy={
            "cell_size_ft": 1.0,
            "buffer_ft": 0.0,
            "max_W": 999,
            "max_H": 999,
            "bounds_crop_fn": crop_fn,
        },
    )

    assert r["reason"] == "crop"
    assert r["confidence"] == "high"
    assert _as_tuple(r["bounds_uv"]) == (0.0, 0.0, 10.0, 20.0)
    assert r["capped"] is False


def test_resolve_bounds_crop_off_extents_failure_falls_back_low_confidence():
    view = _StubView(crop_active=False)
    diag = Diagnostics()

    def extents_fn(_view, _policy):
        raise RuntimeError("extents scan failed")

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

    assert r["reason"] == "fallback"
    assert r["confidence"] == "low"
    assert _as_tuple(r["bounds_uv"]) == (-100.0, -100.0, 100.0, 100.0)


def test_resolve_bounds_cap_triggers_with_before_after_reporting():
    view = _StubView(crop_active=True)
    diag = Diagnostics()

    def crop_fn(_view, _policy):
        # 1000ft x 1000ft => W=1000,H=1000 at cell_size=1
        return Bounds2D(0, 0, 1000, 1000)

    r = resolve_view_bounds(
        view,
        diag=diag,
        policy={
            "cell_size_ft": 1.0,
            "buffer_ft": 0.0,
            "max_W": 100,
            "max_H": 200,
            "bounds_crop_fn": crop_fn,
        },
    )

    assert r["capped"] is True
    assert r["cap_before"]["W"] == 1000
    assert r["cap_before"]["H"] == 1000
    assert r["cap_after"]["W"] == 100
    assert r["cap_after"]["H"] == 200
    # Cap controls resolution (grid dims), not physical bounds clipping.
    assert _as_tuple(r["bounds_uv"]) == (0.0, 0.0, 1000.0, 1000.0)

def _as_tuple(b):
    return (float(b.xmin), float(b.ymin), float(b.xmax), float(b.ymax))
