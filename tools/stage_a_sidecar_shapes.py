"""ONE place that reads a Stage A sidecar field written in more than one shape.

The production writer (``vop_interwoven/color_id_buffer.py``) changes shape;
archive runs keep the old one. Writers change, readers widen: every reader
under ``tools/`` goes through this module rather than keeping its own copy of
"new key, else old key", so the fallback is written once and a test can
compose it with the writer.

LEAF MODULE: standard library only, like ``tools/clamp_pad_geometry.py``, so
the standalone tools that import it do not gain ``vop_interwoven`` as a
dependency.

Three-valued fields come back as ``(state, payload)``: ``payload`` is the value
when ``state == "value"`` and the reason string otherwise.
"""
from __future__ import annotations

from typing import Any

VALUE = "value"
UNAVAILABLE = "unavailable"


def _state_read(raw: Any, null_reason: str) -> tuple[str, Any]:
    if isinstance(raw, dict) and "state" in raw:
        if raw.get("state") == VALUE:
            return (VALUE, raw.get("value"))
        return (raw.get("state") or UNAVAILABLE, raw.get("reason") or "no reason recorded")
    if raw is None:
        return (UNAVAILABLE, null_reason)
    return (VALUE, raw)


def rect_to_corners(rect: Any) -> list[list[float]]:
    """``[umin, vmin, umax, vmax]`` as the four corners pre-C3 sidecars wrote:
    min-min, max-min, max-max, min-max."""
    u0, v0, u1, v1 = rect
    return [[u0, v0], [u1, v0], [u1, v1], [u0, v1]]


def model_entry_uv(entry: dict[str, Any]) -> tuple[str, Any]:
    """A ``near_face_w_map`` entry's UV footprint as ``(state, corners|reason)``.

    C3 writes ``"uv_rect"``: ``[umin, vmin, umax, vmax]``, or a state object
    when the projection failed. Sidecars before it wrote ``"bbox_corners_uv"``:
    the four corners, or null with no reason of its own.
    """
    if "uv_rect" in entry:
        state, payload = _state_read(entry.get("uv_rect"), "uv_rect recorded as null")
        if state == VALUE:
            return (VALUE, rect_to_corners(payload))
        return (state, payload)
    return _state_read(entry.get("bbox_corners_uv"),
                       "recorded as null in the sidecar")


def model_entry_uv_corners(entry: dict[str, Any]) -> list[list[float]] | None:
    """The four UV corners, or None when there are none (either shape)."""
    state, payload = model_entry_uv(entry)
    return payload if state == VALUE else None


def model_entry_bbox_3d(entry: dict[str, Any]) -> tuple[str, Any]:
    """``bbox_3d`` as ``(state, {"min", "max"}|reason)``. C3 writes the plain
    ``{min, max}``; before it, ``{"state": "value", "value": {min, max}}``."""
    return _state_read(entry.get("bbox_3d"), "no bbox_3d recorded")
