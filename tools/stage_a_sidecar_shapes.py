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


# --- C5: one frame record per sidecar ---------------------------------------
#
# Before C5 a model sidecar carried "resolution", "export_frame" and a
# top-level "bounds_xy"; an annotation sidecar carried "resolution". Since C5
# both carry one "frame" record with the exact duplicates written once.
# legacy_view() rebuilds the pre-C5 keys from it EXACTLY, so a reader written
# against the old shape reads a new sidecar unchanged, and an old sidecar
# passes through untouched.

# Keys of the pre-C5 "resolution" block (model and annotation pass).
_RESOLUTION_KEYS = (
    "pixel_size", "requested_pixel_size", "requested_export_dpi",
    "effective_export_dpi", "achieved_export_dpi", "view_scale", "fit_direction",
    "paper_fit_in", "paper_width_in", "paper_height_in", "requested_axis",
    "pre_cap_px", "cap_applied", "max_axis_px", "predicted_derived_px",
    "actual_w", "actual_h", "dim_check", "dim_check_ceiling_px",
    "dim_read_error", "dim_check_attempts", "backoff_stop_reason",
    "backoff_floor_px", "backoff_max_retries",
)
# Keys of the pre-C5 "export_frame" block, besides the ones rebuilt below.
_EXPORT_FRAME_KEYS = (
    "frame_uv", "frame_source", "anno_cap_envelope_applied", "frame_extent_ft",
    "frame_snapped_uv", "frame_px", "crop_px", "crop_offset_px",
    "crop_is_frame", "requested_export_dpi", "achieved_export_dpi",
    "requested_fpp_ft", "achieved_fpp_ft", "pre_cap_px", "cap_applied",
    "max_axis_px", "min_axis_px", "floor_applied_to_crop",
    "cap_applied_by_cap_axes", "lattice_corrections", "verified_against_revit",
)


def frame_record(sidecar: dict[str, Any]) -> dict[str, Any]:
    """The sidecar's frame record, new shape or rebuilt from the old one.

    The pre-C5 keys fold in the same way the writer folds them: resolution
    first, then the export_frame block, then ``bounds_xy`` as ``crop_uv``.
    """
    if isinstance(sidecar.get("frame"), dict):
        return sidecar["frame"]
    out: dict[str, Any] = dict(sidecar.get("resolution") or {})
    out.pop("export_dpi", None)
    out.pop("requested_px", None)
    ef = sidecar.get("export_frame")
    if isinstance(ef, dict):
        for key, value in ef.items():
            if key != "requested_px":
                out.setdefault(key, value)
    if "bounds_xy" in sidecar:
        out["crop_uv"] = sidecar.get("bounds_xy")
    return out


def with_requested(frame: dict[str, Any], run_config: dict[str, Any] | None = None
                   ) -> dict[str, Any]:
    """C6: the frame plus the REQUESTED values it no longer records.

    Exactly derivable from the frame itself:
      requested_pixel_size == dim_check_attempts[0]["requested_px"]
      requested_axis       == "height" if fit_direction == "vertical" else "width"
    From the run's metadata file (C9's run_meta.json, whose "config" holds
    cfg.to_dict(); passed as ``run_config``) when given, never guessed
    without it:
      requested_export_dpi == config["color_id_buffer_export_dpi"]
      requested_fpp_ft     == view_scale / (12 * requested_export_dpi)
    effective_export_dpi is NOT rebuilt here: it is
    ``vop_interwoven.resolution_contract.effective_export_dpi(crop_uv,
    actual_w, actual_h, view_scale)``, and this leaf module does not copy it.
    A pre-C6 frame already carries all of these and is returned unchanged.
    """
    out = dict(frame)
    attempts = frame.get("dim_check_attempts") or []
    if "requested_pixel_size" not in out and attempts:
        out["requested_pixel_size"] = attempts[0].get("requested_px")
    if "requested_axis" not in out and frame.get("fit_direction"):
        out["requested_axis"] = ("height" if frame["fit_direction"] == "vertical"
                                 else "width")
    cfg = (run_config or {}).get("config", run_config) if run_config else None
    if cfg and "requested_export_dpi" not in out:
        dpi = cfg.get("color_id_buffer_export_dpi")
        if dpi:
            out["requested_export_dpi"] = float(dpi)
            if out.get("view_scale") and "status" in out and out["status"] == VALUE:
                out.setdefault("requested_fpp_ft",
                               float(out["view_scale"]) / (12.0 * float(dpi)))
    return out


def legacy_view(sidecar: dict[str, Any], run_config: dict[str, Any] | None = None
                ) -> dict[str, Any]:
    """``sidecar`` with the pre-C5 keys present. Unchanged if it predates C5.

    Since C6 the requested values are rebuilt by with_requested(); pass the
    run's config snapshot to get the requested dpi and fpp back as well."""
    frame = sidecar.get("frame")
    if not isinstance(frame, dict):
        return sidecar
    frame = with_requested(frame, run_config)
    out = dict(sidecar)
    res = {k: frame[k] for k in _RESOLUTION_KEYS if k in frame}
    if "requested_export_dpi" in frame and "crop_uv" in frame:
        res["export_dpi"] = frame["requested_export_dpi"]
    if "requested_pixel_size" in frame and "crop_uv" in frame:
        res["requested_px"] = frame["requested_pixel_size"]
    out.setdefault("resolution", res)
    if "crop_uv" in frame:          # the model pass; the annotation pass has none
        out.setdefault("bounds_xy", frame["crop_uv"])
    if "status" in frame:
        if frame["status"] == VALUE:
            ef = {k: frame[k] for k in _EXPORT_FRAME_KEYS if k in frame}
            ef["status"] = VALUE
            ef["requested_px"] = frame.get("requested_pixel_size")
            ef["crop_snapped_uv"] = frame.get("crop_snapped_uv", frame.get("crop_uv"))
            ef["raster_bounds_uv"] = frame.get("raster_bounds_uv", frame.get("frame_uv"))
        else:
            ef = {"status": frame["status"], "reason": frame.get("reason")}
        out.setdefault("export_frame", ef)
    return out


# --- P1: one capture_integrity record ---------------------------------------

def capture_integrity(sidecar: dict[str, Any]) -> dict[str, Any] | None:
    """The sidecar's capture_integrity record, or one rebuilt from the pre-P1
    keys, or None when the sidecar carries neither (so "absent" is never read
    as "clean").

    A rebuilt record says so (``status: "rebuilt_from_pre_p1_keys"``), and a
    field the old shape never recorded is an "unavailable" state, not a zero:
    the pre-P1 model pass wrote no restore-failure list and no fault list.
    """
    if isinstance(sidecar.get("capture_integrity"), dict):
        return sidecar["capture_integrity"]
    old_keys = ("capture_faults", "restore_failures", "paint_failures", "registration_marks")
    if not any(k in sidecar for k in old_keys):
        return None

    def _missing(what):
        return {"state": UNAVAILABLE,
                "reason": "the pre-P1 sidecar recorded no {0}".format(what)}
    restore = ((sidecar.get("registration_marks") or {}).get("restore") or {})
    marks_left = restore.get("marks_still_in_project")
    return {
        "status": "rebuilt_from_pre_p1_keys",
        "rolled_back": (restore["rolled_back"] if "rolled_back" in restore
                        else _missing("rollback verdict")),
        "marks_still_in_project": (len(marks_left) if isinstance(marks_left, (list, dict))
                                   else _missing("marks_still_in_project")),
        "restore_failures": (len(sidecar["restore_failures"])
                             if isinstance(sidecar.get("restore_failures"), list)
                             else _missing("restore_failures")),
        "capture_faults": (list(sidecar["capture_faults"])
                           if isinstance(sidecar.get("capture_faults"), list)
                           else _missing("capture_faults")),
        "paint_failures": (sidecar["paint_failures"] if "paint_failures" in sidecar
                           else _missing("paint_failures")),
    }
