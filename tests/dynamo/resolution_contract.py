"""Pure Stage A probe resolution helpers for safe non-Revit tests."""
from __future__ import annotations

import json
import math


def round_half_up_positive(value):
    value = float(value)
    if value < 0:
        raise ValueError("round_half_up_positive requires a nonnegative value")
    return int(math.floor(value + 0.5))


def _positive(value, name):
    if value is None or float(value) <= 0:
        raise ValueError(f"{name} must be positive")
    return float(value)


def calculate_paper_space_resolution(model_width_ft, model_height_ft, view_scale, target_dpi, bounds_source):
    model_width_ft = _positive(model_width_ft, "model_width_ft")
    model_height_ft = _positive(model_height_ft, "model_height_ft")
    view_scale = int(_positive(view_scale, "view_scale"))
    target_dpi = _positive(target_dpi, "target_dpi")
    paper_width_in = model_width_ft * 12.0 / view_scale
    requested_width_px = round_half_up_positive(paper_width_in * target_dpi)
    ppf = 12.0 * target_dpi / view_scale
    return {
        "policy": "paper_space_dpi",
        "target_dpi": target_dpi,
        "view_scale": view_scale,
        "bounds_source": bounds_source,
        "model_width_ft": model_width_ft,
        "model_height_ft": model_height_ft,
        "paper_width_in": paper_width_in,
        "requested_width_px": requested_width_px,
        "accepted_width_px": requested_width_px,
        "predicted_height_px": round_half_up_positive(model_height_ft * ppf),
        "actual_width_px": None,
        "actual_height_px": None,
        "target_pixels_per_model_foot": ppf,
        "accepted_pixels_per_model_foot": ppf,
        "effective_dpi": target_dpi,
        "target_model_inches_per_pixel": 12.0 / ppf,
        "actual_model_inches_per_pixel": 12.0 / ppf,
        "max_pixel_dimension": None,
        "capped": False,
    }


def apply_resolution_cap(report, max_pixel_dimension):
    cap = None if max_pixel_dimension is None else int(_positive(max_pixel_dimension, "max_pixel_dimension"))
    report = dict(report, max_pixel_dimension=cap)
    if cap is None:
        return report
    width = report["requested_width_px"]
    height = report["predicted_height_px"]
    factor = min(1.0, cap / width, cap / height)
    if factor < 1:
        accepted = max(1, round_half_up_positive(width * factor))
        report["accepted_width_px"] = accepted
        report["predicted_height_px"] = max(1, round_half_up_positive(height * factor))
        report["accepted_pixels_per_model_foot"] = accepted / report["model_width_ft"]
        report["effective_dpi"] = report["accepted_pixels_per_model_foot"] * report["view_scale"] / 12.0
        report["actual_model_inches_per_pixel"] = 12.0 / report["accepted_pixels_per_model_foot"]
        report["capped"] = True
    return report


def calculate_canvas_placement(model_bounds, canvas_bounds, accepted_pixels_per_model_foot):
    ppf = _positive(accepted_pixels_per_model_foot, "accepted_pixels_per_model_foot")
    mu0, mv0, mu1, mv1 = model_bounds
    cu0, cv0, cu1, cv1 = canvas_bounds
    values = [(cu1 - cu0) * ppf, (cv1 - cv0) * ppf, (mu0 - cu0) * ppf, (cv1 - mv1) * ppf]
    rounded = [round_half_up_positive(v) for v in values]
    errors = [abs(v - r) for v, r in zip(values, rounded)]
    return {
        "canvas_width_px": rounded[0],
        "canvas_height_px": rounded[1],
        "model_offset_px": [rounded[2], rounded[3]],
        "model_offset_float_px": [values[2], values[3]],
        "rounding_error_px": {"max": max(errors)},
        "rounding_error_model_units": max(errors) / ppf,
        "lossless_padding_possible": max(errors) < 1e-9,
        "resampling_required": max(errors) >= 1e-9,
    }


def build_resolution_report(*args, actual_width_px=None, actual_height_px=None, max_pixel_dimension=None, **kwargs):
    report = calculate_paper_space_resolution(*args, **kwargs)
    report = apply_resolution_cap(report, max_pixel_dimension)
    report["actual_width_px"] = actual_width_px
    report["actual_height_px"] = actual_height_px
    json.dumps(report)
    return report


def resolution_report_for_accepted_width(report, accepted_width_px):
    report = dict(report)
    accepted_width_px = int(_positive(accepted_width_px, "accepted_width_px"))
    model_width = _positive(report["model_width_ft"], "model_width_ft")
    model_height = _positive(report["model_height_ft"], "model_height_ft")
    report["accepted_width_px"] = accepted_width_px
    report["accepted_pixels_per_model_foot"] = accepted_width_px / model_width
    report["predicted_height_px"] = round_half_up_positive(model_height * report["accepted_pixels_per_model_foot"])
    report["effective_dpi"] = report["accepted_pixels_per_model_foot"] * report["view_scale"] / 12.0
    report["actual_model_inches_per_pixel"] = 12.0 / report["accepted_pixels_per_model_foot"]
    if accepted_width_px != report["requested_width_px"]:
        report["pixel_size_backoff"] = True
    json.dumps(report)
    return report


def choose_resolution_bounds(active_crop_bounds=None, resolved_model_bounds=None, canvas_bounds=None):
    if active_crop_bounds is not None:
        return active_crop_bounds, "active_model_crop"
    if resolved_model_bounds is not None:
        return resolved_model_bounds, "resolved_model_bounds"
    if canvas_bounds is not None:
        return canvas_bounds, "canvas_bounds"
    return None, "INCONCLUSIVE"
