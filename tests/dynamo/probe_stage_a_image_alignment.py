# -*- coding: utf-8 -*-
"""Standalone Dynamo/Revit 2025 probe for Stage A image geometry alignment.

Dynamo inputs:
    IN[0] target view
    IN[1] output directory
    IN[2] export mode: "original", "model_bounds", "canvas_bounds", or "all"
    IN[3] create temporary calibration markers (bool, default True)

This file is intentionally self-contained and does not modify production Stage A
code.  It reuses the proven safety shape from
probe_stage_a_transaction_group_export.py: commit temporary child transactions,
export while no child transaction is open, and roll back the enclosing
TransactionGroup in finally.
"""
from __future__ import print_function

import hashlib
import json
import math
import os
import re
import sys
import time


DEFAULT_FIXED_PIXEL_WIDTH = 1600
DEFAULT_RESOLUTION_POLICY = "paper_space_dpi"
DEFAULT_TARGET_DPI = 150
DEFAULT_FIXED_PIXEL_WIDTH = 1600
DEFAULT_MAX_PIXEL_DIMENSION = None

_PROBE_CONTRACT = None


def _probe_contract():
    """Import the shared contract after the repository path has been bootstrapped."""
    global _PROBE_CONTRACT
    if _PROBE_CONTRACT is None:
        try:
            import tests.dynamo.stage_a_probe_contract as contract
        except ImportError:
            import stage_a_probe_contract as contract
        _PROBE_CONTRACT = contract
    return _PROBE_CONTRACT


def round_half_up_positive(value):
    value = float(value)
    if value < 0:
        raise ValueError("round_half_up_positive requires a nonnegative value")
    return int(math.floor(value + 0.5))


def _positive_float(value, name, allow_none=False):
    if value is None:
        if allow_none:
            return None
        raise ValueError("{0} is required".format(name))
    number = float(value)
    if number <= 0:
        raise ValueError("{0} must be positive".format(name))
    return number


def _parse_resolution_policy(value):
    text = str(value or "paper_space_dpi").strip().lower()
    if text not in ("fixed_pixel_width", "paper_space_dpi", "both"):
        raise ValueError("Unsupported resolution_policy '{0}'".format(value))
    return text


def _parse_dpi_values(value):
    if value is None or value == "":
        return [150.0]
    if isinstance(value, (list, tuple)):
        raw = value
    else:
        raw = str(value).replace(";", ",").split(",")
    values = []
    for item in raw:
        if item is None or str(item).strip() == "":
            continue
        values.append(_positive_float(item, "target_dpi"))
    return values or [150.0]


def _parse_optional_cap(value):
    if value is None or value == "":
        return None
    return int(round_half_up_positive(_positive_float(value, "max_pixel_dimension")))


def _resolution_runs(policy, dpi_value, fixed_width, max_dimension):
    policy = _parse_resolution_policy(policy)
    fixed = int(round_half_up_positive(_positive_float(fixed_width if fixed_width is not None else 1600, "fixed_pixel_width")))
    cap = _parse_optional_cap(max_dimension)
    runs = []
    if policy in ("paper_space_dpi", "both"):
        for dpi in _parse_dpi_values(dpi_value):
            runs.append({"policy": "paper_space_dpi", "target_dpi": float(dpi), "fixed_pixel_width": fixed, "max_pixel_dimension": cap})
    if policy in ("fixed_pixel_width", "both"):
        runs.append({"policy": "fixed_pixel_width", "target_dpi": None, "fixed_pixel_width": fixed, "max_pixel_dimension": cap})
    return runs


def _resolution_suffix(run):
    if run.get("policy") == "fixed_pixel_width":
        return "fixed_{0}".format(int(run.get("fixed_pixel_width")))
    dpi = run.get("target_dpi")
    return "dpi_{0}".format(int(dpi) if abs(float(dpi) - int(float(dpi))) < 1e-9 else str(dpi).replace(".", "p"))


def calculate_paper_space_resolution(model_width_ft, model_height_ft, view_scale, target_dpi, bounds_source):
    model_width_ft = _positive_float(model_width_ft, "model_width_ft")
    model_height_ft = _positive_float(model_height_ft, "model_height_ft")
    view_scale = int(round_half_up_positive(_positive_float(view_scale, "view_scale")))
    target_dpi = _positive_float(target_dpi, "target_dpi")
    paper_width_in = model_width_ft * 12.0 / float(view_scale)
    requested_width_px = round_half_up_positive(paper_width_in * target_dpi)
    pixels_per_model_foot = 12.0 * target_dpi / float(view_scale)
    predicted_height_px = round_half_up_positive(model_height_ft * pixels_per_model_foot)
    return {"policy": "paper_space_dpi", "target_dpi": float(target_dpi), "view_scale": view_scale, "bounds_source": str(bounds_source), "model_width_ft": float(model_width_ft), "model_height_ft": float(model_height_ft), "paper_width_in": paper_width_in, "requested_width_px": int(requested_width_px), "accepted_width_px": int(requested_width_px), "predicted_height_px": int(predicted_height_px), "target_pixels_per_model_foot": pixels_per_model_foot, "accepted_pixels_per_model_foot": pixels_per_model_foot, "effective_dpi": float(target_dpi), "target_model_inches_per_pixel": 12.0 / pixels_per_model_foot, "actual_model_inches_per_pixel": 12.0 / pixels_per_model_foot, "max_pixel_dimension": None, "capped": False}


def apply_resolution_cap(report, max_pixel_dimension):
    cap = _parse_optional_cap(max_pixel_dimension)
    report = dict(report)
    report["max_pixel_dimension"] = cap
    if cap is None:
        return report
    width = int(report["requested_width_px"])
    height = int(report["predicted_height_px"])
    factor = min(1.0, float(cap) / float(width), float(cap) / float(height))
    if factor < 1.0:
        accepted = max(1, round_half_up_positive(width * factor))
        report["accepted_width_px"] = int(accepted)
        report["predicted_height_px"] = max(1, round_half_up_positive(height * factor))
        report["accepted_pixels_per_model_foot"] = float(accepted) / float(report["model_width_ft"])
        report["effective_dpi"] = report["accepted_pixels_per_model_foot"] * float(report["view_scale"]) / 12.0
        report["actual_model_inches_per_pixel"] = 12.0 / report["accepted_pixels_per_model_foot"]
        report["capped"] = True
    return report


def build_resolution_report(policy, model_width_ft, model_height_ft, view_scale, target_dpi, bounds_source, fixed_pixel_width, max_pixel_dimension=None):
    if policy == "fixed_pixel_width":
        model_width_ft = _positive_float(model_width_ft, "model_width_ft")
        model_height_ft = _positive_float(model_height_ft, "model_height_ft")
        fixed = int(round_half_up_positive(_positive_float(fixed_pixel_width, "fixed_pixel_width")))
        ppf = float(fixed) / model_width_ft
        return {"policy": "fixed_pixel_width", "target_dpi": None, "view_scale": int(view_scale) if view_scale else None, "bounds_source": str(bounds_source), "model_width_ft": float(model_width_ft), "model_height_ft": float(model_height_ft), "paper_width_in": None, "requested_width_px": fixed, "accepted_width_px": fixed, "predicted_height_px": round_half_up_positive(model_height_ft * ppf), "target_pixels_per_model_foot": ppf, "accepted_pixels_per_model_foot": ppf, "effective_dpi": (ppf * float(view_scale) / 12.0) if view_scale else None, "target_model_inches_per_pixel": 12.0 / ppf, "actual_model_inches_per_pixel": 12.0 / ppf, "max_pixel_dimension": None, "capped": False}
    return apply_resolution_cap(calculate_paper_space_resolution(model_width_ft, model_height_ft, view_scale, target_dpi, bounds_source), max_pixel_dimension)


def _actual_tiff_dimensions(path):
    try:
        from PIL import Image
        with Image.open(path) as img:
            return int(img.size[0]), int(img.size[1])
    except Exception:
        return None, None


def _finalize_resolution_report(report, actual_width, actual_height):
    report = dict(report)
    report["actual_width_px"] = int(actual_width) if actual_width else None
    report["actual_height_px"] = int(actual_height) if actual_height else None
    report["dimension_discrepancy"] = {"width_px": (int(actual_width) - int(report["accepted_width_px"])) if actual_width else None, "height_px": (int(actual_height) - int(report["predicted_height_px"])) if actual_height else None}
    return report


def _resolution_report_for_accepted_width(report, accepted_width):
    report = dict(report)
    accepted_width = int(round_half_up_positive(_positive_float(accepted_width, "accepted_width_px")))
    model_width = _positive_float(report.get("model_width_ft"), "model_width_ft")
    model_height = _positive_float(report.get("model_height_ft"), "model_height_ft")
    report["accepted_width_px"] = accepted_width
    report["accepted_pixels_per_model_foot"] = float(accepted_width) / model_width
    report["predicted_height_px"] = round_half_up_positive(model_height * report["accepted_pixels_per_model_foot"])
    view_scale = report.get("view_scale")
    report["effective_dpi"] = (report["accepted_pixels_per_model_foot"] * float(view_scale) / 12.0) if view_scale else None
    report["actual_model_inches_per_pixel"] = 12.0 / report["accepted_pixels_per_model_foot"]
    if accepted_width != int(report.get("requested_width_px", accepted_width)):
        report["pixel_size_backoff"] = True
    return report


def calculate_canvas_placement(model_bounds, canvas_bounds, accepted_pixels_per_model_foot):
    if not model_bounds or not canvas_bounds:
        return {"available": False, "reason": "missing model or canvas bounds"}
    mb = _bounds_tuple(model_bounds); cb = _bounds_tuple(canvas_bounds)
    density = _positive_float(accepted_pixels_per_model_foot, "accepted_pixels_per_model_foot")
    canvas_w = (cb[2] - cb[0]) * density; canvas_h = (cb[3] - cb[1]) * density
    off_x = (mb[0] - cb[0]) * density; off_y = (cb[3] - mb[3]) * density
    vals = [canvas_w, canvas_h, off_x, off_y]
    rounded = [round_half_up_positive(v) for v in vals]
    errors = [abs(vals[i] - rounded[i]) for i in range(len(vals))]
    return {"available": True, "canvas_width_px": rounded[0], "canvas_height_px": rounded[1], "model_offset_px": [rounded[2], rounded[3]], "model_offset_float_px": [off_x, off_y], "model_offset_fractional_px": [off_x - math.floor(off_x), off_y - math.floor(off_y)], "rounding_error_px": {"canvas_width": errors[0], "canvas_height": errors[1], "offset_x": errors[2], "offset_y": errors[3], "max": max(errors)}, "rounding_error_model_units": max(errors) / density, "lossless_padding_possible": max(errors) < 1e-9, "resampling_required": max(errors) >= 1e-9}

SUPPORTED_MODES = ("original", "model_bounds", "canvas_bounds", "all")


def select_resolution_runs(resolution_cases, resolution_policy, target_dpi,
                           fixed_pixel_width, max_pixel_dimension):
    available = _resolution_runs(resolution_policy, target_dpi, fixed_pixel_width, max_pixel_dimension)
    names = _probe_contract().select_named(resolution_cases, [_resolution_suffix(item) for item in available], "resolution case(s)")
    return [item for item in available if _resolution_suffix(item) in names]
MARKER_SPECS = [
    ("lower_left",  (255, 0, 0)),
    ("lower_right", (0, 255, 0)),
    ("upper_left",  (0, 0, 255)),
    ("upper_right", (255, 0, 255)),
    ("center",      (0, 255, 255)),
]


def _candidate_repo_roots(output_dir=None):
    """Yield possible repo roots without relying on ``__file__``.

    Dynamo CPython executes pasted node code without defining ``__file__``.
    Prefer already-working imports, then search stable runtime anchors.
    """
    seen = set()

    def emit(path):
        if not path:
            return
        try:
            path = os.path.abspath(os.path.expanduser(str(path)))
        except Exception:
            return
        if path in seen:
            return
        seen.add(path)
        yield path

    anchors = []
    for env_name in ("REVIT_SSM_EXPORTER_ROOT", "VOP_REPO_ROOT"):
        try:
            anchors.append(os.environ.get(env_name))
        except Exception:
            pass
    anchors.extend([output_dir, os.getcwd()])
    try:
        anchors.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        # Expected in Dynamo CPython pasted-node execution.
        pass
    for anchor in anchors:
        if not anchor:
            continue
        cur = os.path.abspath(os.path.expanduser(str(anchor)))
        if os.path.isfile(cur):
            cur = os.path.dirname(cur)
        for _ in range(8):
            for out in emit(cur):
                yield out
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    home = os.path.expanduser("~")
    common = [
        os.path.join(home, "Documents", "Revit_SSM_Exporter"),
        os.path.join(home, "Documents", "GitHub", "Revit_SSM_Exporter"),
        os.path.join(home, "source", "repos", "Revit_SSM_Exporter"),
        os.path.join(home, "Revit_SSM_Exporter"),
        "/workspace/Revit_SSM_Exporter",
    ]
    for path in common:
        for out in emit(path):
            yield out


def _ensure_contract_import_path(output_dir=None):
    """Add the checkout containing the shared probe contract to ``sys.path``."""
    checked = []
    for root in _candidate_repo_roots(output_dir=output_dir):
        checked.append(root)
        if os.path.isfile(os.path.join(root, "tests", "dynamo", "stage_a_probe_contract.py")):
            for candidate in (root, os.path.join(root, "tests", "dynamo")):
                if candidate not in sys.path:
                    sys.path.insert(0, candidate)
            return root
    raise RuntimeError(
        "Could not locate tests/dynamo/stage_a_probe_contract.py. "
        "Set REVIT_SSM_EXPORTER_ROOT or place the output directory inside the checkout. "
        "Checked: {0}".format(checked)
    )


def _ensure_repo_on_path(output_dir=None):
    """Put the repo root on sys.path in Dynamo, where __file__ may not exist."""
    try:
        import vop_interwoven  # noqa: F401
        return None
    except Exception:
        pass
    checked = []
    for root in _candidate_repo_roots(output_dir=output_dir):
        checked.append(root)
        if os.path.isdir(os.path.join(root, "vop_interwoven")):
            if root not in sys.path:
                sys.path.insert(0, root)
            try:
                import vop_interwoven  # noqa: F401
                return root
            except Exception:
                continue
    raise RuntimeError(
        "Could not locate Revit_SSM_Exporter repo root for vop_interwoven imports. "
        "Set environment variable REVIT_SSM_EXPORTER_ROOT to the repo folder, "
        "or run Dynamo with IN[1] inside the repo/output tree. Checked: {0}".format(checked)
    )


def _ensure_revit_api_reference():
    try:
        import clr
        clr.AddReference("RevitAPI")
        clr.AddReference("RevitServices")
    except Exception:
        pass


def _unwrap(value):
    # Dynamo may pass either a Dynamo wrapper or an Autodesk.Revit.DB element.
    # Return the DB element; do not call ToDSType here because that wraps DB
    # elements for Dynamo output and hides DB-only members needed by the probe.
    try:
        return value.InternalElement
    except Exception:
        return value


def _safe_int_id(elem_id):
    if elem_id is None:
        return None
    for attr in ("IntegerValue", "Value"):
        try:
            return int(getattr(elem_id, attr))
        except Exception:
            pass
    try:
        return int(str(elem_id))
    except Exception:
        return None


def _xyz(xyz):
    if xyz is None:
        return None
    return [float(xyz.X), float(xyz.Y), float(xyz.Z)]


def _transform_dict(t):
    if t is None:
        return None
    return {"origin": _xyz(t.Origin), "basis_x": _xyz(t.BasisX), "basis_y": _xyz(t.BasisY), "basis_z": _xyz(t.BasisZ)}


def _bounds_tuple(b):
    if b is None:
        return None
    if isinstance(b, (list, tuple)):
        return [float(x) for x in b]
    return [float(b.xmin), float(b.ymin), float(b.xmax), float(b.ymax)]


def _bounds_dict(b):
    t = _bounds_tuple(b)
    if t is None:
        return None
    return {"u_min": t[0], "v_min": t[1], "u_max": t[2], "v_max": t[3], "width": t[2] - t[0], "height": t[3] - t[1]}


def _make_bounds(t):
    from vop_interwoven.core.math_utils import Bounds2D
    return Bounds2D(float(t[0]), float(t[1]), float(t[2]), float(t[3]))


def _union_bounds(a, b):
    if a is None:
        return b
    if b is None:
        return a
    aa, bb = _bounds_tuple(a), _bounds_tuple(b)
    return _make_bounds((min(aa[0], bb[0]), min(aa[1], bb[1]), max(aa[2], bb[2]), max(aa[3], bb[3])))


def _intersect_bounds(bounds_list):
    usable = [_bounds_tuple(b) for b in bounds_list if b is not None]
    if not usable:
        return None
    u0 = max(b[0] for b in usable)
    v0 = max(b[1] for b in usable)
    u1 = min(b[2] for b in usable)
    v1 = min(b[3] for b in usable)
    if u1 <= u0 or v1 <= v0:
        return None
    return _make_bounds((u0, v0, u1, v1))


def _sanitize_filename(name):
    safe = re.sub(r"[^A-Za-z0-9_. -]+", "_", name or "view").strip(" .")
    return safe or "view"


def _capture_crop_state(view):
    state = {}
    for attr in ("CropBoxActive", "CropBoxVisible", "AnnotationCropActive", "DisplayStyle", "DetailLevel", "ViewTemplateId"):
        try:
            v = getattr(view, attr)
            if attr == "ViewTemplateId":
                v = _safe_int_id(v)
            else:
                v = str(v) if attr in ("DisplayStyle", "DetailLevel") else bool(v)
            state[attr] = v
        except Exception as ex:
            state[attr] = {"error": str(ex)}
    try:
        cb = view.CropBox
        state["CropBox"] = {"min": _xyz(cb.Min), "max": _xyz(cb.Max), "transform": _transform_dict(getattr(cb, "Transform", None))}
    except Exception as ex:
        state["CropBox"] = {"error": str(ex)}
    return state


def _document_manager_doc():
    _ensure_revit_api_reference()
    from RevitServices.Persistence import DocumentManager
    return DocumentManager.Instance.CurrentDBDocument


def _force_close_dynamo_transaction():
    _ensure_revit_api_reference()
    from RevitServices.Transactions import TransactionManager
    TransactionManager.Instance.ForceCloseTransaction()


def _reject_reason(view):
    try:
        from Autodesk.Revit.DB import ViewType
        if view is None:
            return "IN[0] target view is required"
        if bool(getattr(view, "IsTemplate", False)):
            return "View templates are unsupported"
        vt = getattr(view, "ViewType", None)
        if vt in (getattr(ViewType, "ThreeD", None), getattr(ViewType, "Schedule", None), getattr(ViewType, "DrawingSheet", None)):
            return "Unsupported view type: {0}".format(vt)
        if hasattr(view, "IsPerspective") and bool(view.IsPerspective):
            return "Perspective views are unsupported"
        from vop_interwoven.revit.view_basis import resolve_view_mode, VIEW_MODE_MODEL_AND_ANNOTATION
        mode, reason = resolve_view_mode(view)
        if mode != VIEW_MODE_MODEL_AND_ANNOTATION:
            return "Annotation-only or rejected views are unsupported for this Stage A model-geometry probe: {0}".format(reason.get("why"))
    except Exception as ex:
        return "Could not validate view capability: {0}".format(ex)
    return None


def _config_for_view(view):
    from vop_interwoven.config import Config
    cfg = Config()
    if not hasattr(cfg, "cell_size_paper_in") or cfg.cell_size_paper_in is None:
        cfg.cell_size_paper_in = 0.125
    return cfg


def _compute_bounds(doc, view, cfg):
    from vop_interwoven.revit.view_basis import make_view_basis, resolve_view_bounds, xy_bounds_from_crop_box_all_corners
    from vop_interwoven.revit.annotation import compute_annotation_extents
    basis = make_view_basis(view)
    scale = int(getattr(view, "Scale", 1) or 1)
    requested_cell = (float(cfg.cell_size_paper_in) * float(scale)) / 12.0
    policy = {"doc": doc, "basis": basis, "cfg": cfg, "buffer_ft": float(getattr(cfg, "bounds_buffer_ft", 0.0) or 0.0), "cell_size_ft": requested_cell, "max_W": getattr(cfg, "max_grid_cells_width", None), "max_H": getattr(cfg, "max_grid_cells_height", None)}
    resolved = resolve_view_bounds(view, policy=policy)
    original_crop_bounds = None
    try:
        original_crop_bounds = xy_bounds_from_crop_box_all_corners(view, basis, buffer=0.0)
    except Exception:
        original_crop_bounds = None
    resolved_bounds = resolved.get("bounds_uv")
    model_bounds = resolved.get("model_bounds_uv")
    model_bounds_source = "resolve_view_bounds.model_bounds_uv" if model_bounds is not None else None
    if model_bounds is None and bool(getattr(view, "CropBoxActive", False)):
        model_bounds = original_crop_bounds
        model_bounds_source = "active_original_crop_fallback" if model_bounds is not None else None
    # For inactive crops, keep model_bounds as None when resolve_view_bounds() did
    # not produce model_bounds_uv. resolved bounds may already include annotation
    # expansion, so labeling them as model bounds makes model/canvas placement a
    # tautology and hides the case this probe must measure.
    annotation_bounds = None
    try:
        annotation_seed = model_bounds if model_bounds is not None else resolved_bounds
        if annotation_seed is not None:
            annotation_bounds = compute_annotation_extents(doc, view, basis, annotation_seed, requested_cell, cfg=cfg)
    except Exception:
        annotation_bounds = None
    canvas_bounds = _union_bounds(model_bounds, annotation_bounds) or resolved_bounds
    return basis, requested_cell, resolved, original_crop_bounds, model_bounds, model_bounds_source, annotation_bounds, canvas_bounds


def _uv_to_world_xyz(basis, u, v):
    from Autodesk.Revit.DB import XYZ
    ox, oy, oz = basis.origin
    rx, ry, rz = basis.right
    ux, uy, uz = basis.up
    return XYZ(ox + u * rx + v * ux, oy + u * ry + v * uy, oz + u * rz + v * uz)


def _build_marker_ogs(color):
    from Autodesk.Revit.DB import OverrideGraphicSettings, Color
    ogs = OverrideGraphicSettings()
    c = Color(int(color[0]), int(color[1]), int(color[2]))
    ogs.SetProjectionLineColor(c)
    try:
        ogs.SetCutLineColor(c)
    except Exception:
        pass
    return ogs


def _create_calibration_markers(doc, view, bounds, basis, marker_size_ft):
    from Autodesk.Revit.DB import Line, Transaction, DetailCurve
    bt = _bounds_tuple(bounds)
    if bt is None:
        return [], [{"message": "No bounds available for markers"}]
    u0, v0, u1, v1 = bt
    du, dv = u1 - u0, v1 - v0
    inset_u = max(du * 0.05, marker_size_ft * 2.0)
    inset_v = max(dv * 0.05, marker_size_ft * 2.0)
    positions = {
        "lower_left": (u0 + inset_u, v0 + inset_v),
        "lower_right": (u1 - inset_u, v0 + inset_v),
        "upper_left": (u0 + inset_u, v1 - inset_v),
        "upper_right": (u1 - inset_u, v1 - inset_v),
        "center": ((u0 + u1) * 0.5, (v0 + v1) * 0.5),
    }
    tx = Transaction(doc, "VOP probe create calibration markers")
    tx.Start()
    markers = []
    diagnostics = []
    try:
        for name, rgb in MARKER_SPECS:
            u, v = positions[name]
            p1 = _uv_to_world_xyz(basis, u - marker_size_ft * 0.5, v, )
            p2 = _uv_to_world_xyz(basis, u + marker_size_ft * 0.5, v, )
            p3 = _uv_to_world_xyz(basis, u, v - marker_size_ft * 0.5, )
            p4 = _uv_to_world_xyz(basis, u, v + marker_size_ft * 0.5, )
            created = []
            for a, b in ((p1, p2), (p3, p4)):
                dc = doc.Create.NewDetailCurve(view, Line.CreateBound(a, b))
                view.SetElementOverrides(dc.Id, _build_marker_ogs(rgb))
                created.append(_safe_int_id(dc.Id))
            markers.append({"name": name, "uv": [float(u), float(v)], "rgb": list(rgb), "element_ids": created, "mechanism": "DetailCurve cross"})
        tx.Commit()
    except Exception as ex:
        diagnostics.append({"message": "DetailCurve marker creation failed; falling back to geometry/content measurement", "error": str(ex)})
        try:
            tx.RollBack()
        except Exception:
            pass
        return [], diagnostics
    return markers, diagnostics


def _set_crop_to_bounds(doc, view, basis, bounds, label):
    from Autodesk.Revit.DB import Transaction
    from vop_interwoven.revit.view_basis import crop_box_from_uv_bounds
    if bounds is None:
        return {"changed": False, "reason": "bounds unavailable"}
    cb = crop_box_from_uv_bounds(view, basis, *_bounds_tuple(bounds))
    if cb is None:
        return {"changed": False, "reason": "crop_box_from_uv_bounds returned None"}
    tx = Transaction(doc, "VOP probe crop {0}".format(label))
    tx.Start()
    try:
        view.CropBox = cb
        view.CropBoxActive = True
        try:
            view.CropBoxVisible = True
        except Exception:
            pass
        tx.Commit()
        return {"changed": True, "reason": "committed child transaction"}
    except Exception as ex:
        try:
            tx.RollBack()
        except Exception:
            pass
        return {"changed": False, "reason": str(ex)}


def _set_pixel_size_with_backoff(opts, requested):
    candidate = max(1, int(requested))
    while True:
        try:
            opts.PixelSize = candidate
            return candidate
        except Exception:
            if candidate <= 16:
                raise
            candidate = max(16, candidate // 2)


def _discover_and_rename_tiff(out_dir, before, final_path):
    after = set(os.listdir(out_dir))
    candidates = [f for f in (after - before) if f.lower().endswith((".tif", ".tiff"))]
    if not candidates:
        raise RuntimeError("ExportImage produced no new TIFF in {0}".format(out_dir))
    candidates.sort(key=lambda n: os.path.getmtime(os.path.join(out_dir, n)), reverse=True)
    created = os.path.join(out_dir, candidates[0])
    if os.path.exists(final_path):
        os.remove(final_path)
    os.rename(created, final_path)
    return final_path


def _export_tiff(doc, view, output_path, requested_pixel_size):
    _ensure_revit_api_reference()
    from Autodesk.Revit.DB import ImageExportOptions, ExportRange, ZoomFitType, FitDirectionType, ElementId, ImageFileType
    import System.Collections.Generic as SCG
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    before = set(os.listdir(out_dir))
    ids = SCG.List[ElementId]()
    ids.Add(view.Id)
    opts = ImageExportOptions()
    opts.ExportRange = ExportRange.SetOfViews
    opts.SetViewsAndSheets(ids)
    opts.ZoomType = ZoomFitType.FitToPage
    opts.FitDirection = FitDirectionType.Horizontal
    accepted = _set_pixel_size_with_backoff(opts, requested_pixel_size)
    opts.FilePath = os.path.join(out_dir, "_vop_stage_a_alignment_tmp")
    tiff_type = getattr(ImageFileType, "TIFF", getattr(ImageFileType, "TIF", None))
    if tiff_type is None:
        raise RuntimeError("Revit ImageFileType does not expose TIFF/TIF")
    opts.HLRandWFViewsFileType = tiff_type
    opts.ShadowViewsFileType = tiff_type
    doc.ExportImage(opts)
    return _discover_and_rename_tiff(out_dir, before, output_path), accepted


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _analyze_image(path, markers, bounds):
    res = {"path": path, "file_size": None, "sha256": None, "status": "pending_external_analysis"}
    if os.path.exists(path):
        res["file_size"] = os.path.getsize(path)
        res["sha256"] = _sha256(path)
    res["marker_analysis_available"] = False
    res["diagnostic"] = "Pixel marker detection and residual analysis moved to tools/analyze_stage_a_probe.py"
    return res

def _content_width_px(model_img):
    content = model_img.get("content_rect_px") if model_img else None
    if content and len(content) == 4:
        return max(1, int(content[2]) - int(content[0]) + 1)
    return int(model_img.get("actual_width") or 0) if model_img else 0


def _placement(model_bounds, canvas_bounds, model_img):
    content_width = _content_width_px(model_img)
    if not model_bounds or not canvas_bounds or not model_img or not content_width:
        return {"available": False, "reason": "missing model/canvas bounds or content image dimensions"}
    mb, cb = _bounds_tuple(model_bounds), _bounds_tuple(canvas_bounds)
    density = float(content_width) / max(1e-9, mb[2] - mb[0])
    canvas_w = (cb[2] - cb[0]) * density
    canvas_h = (cb[3] - cb[1]) * density
    off_x = (mb[0] - cb[0]) * density
    off_y = (cb[3] - mb[3]) * density
    vals = [canvas_w, canvas_h, off_x, off_y]
    rounding = [abs(v - round(v)) for v in vals]
    return {"available": True, "observed_px_per_model_unit": density, "density_source_content_width_px": int(content_width), "tiff_actual_width_px": int(model_img.get("actual_width") or 0), "canvas_pixel_dimensions_float": [canvas_w, canvas_h], "model_image_offset_float": [off_x, off_y], "offset_integral": [rounding[2] < 1e-6, rounding[3] < 1e-6], "max_rounding_error_px": max(rounding), "max_rounding_error_model_units": max(rounding) / density, "lossless_padding_sufficient": max(rounding) < 1e-6, "resampling_required_if_exact_canvas_needed": max(rounding) >= 1e-6}


def _run_native(raw_view, output_dir=None, mode="all", create_markers=True,
              resolution_policy=DEFAULT_RESOLUTION_POLICY, target_dpi=DEFAULT_TARGET_DPI,
              fixed_pixel_width=DEFAULT_FIXED_PIXEL_WIDTH,
              max_pixel_dimension=DEFAULT_MAX_PIXEL_DIMENSION,
              resolution_cases="all", repetition_count=2):
    """Execute selected alignment cases; owns and cleans up its TransactionGroup."""
    out_dir = output_dir or os.path.join(os.path.expanduser("~"), "Desktop")
    _ensure_repo_on_path(output_dir=out_dir)
    _ensure_revit_api_reference()
    from Autodesk.Revit.DB import TransactionGroup, TransactionStatus
    doc = _document_manager_doc()
    view = _unwrap(raw_view)
    mode = str(mode or "all").strip().lower()
    repetition_count = int(repetition_count)
    if repetition_count < 1:
        raise ValueError("repetition_count must be at least 1")
    selected_runs = select_resolution_runs(resolution_cases, resolution_policy, target_dpi, fixed_pixel_width, max_pixel_dimension)
    selected_case_names = [_resolution_suffix(item) for item in selected_runs]
    if mode not in SUPPORTED_MODES:
        raise ValueError("Unsupported export mode '{0}'. Expected one of {1}".format(mode, SUPPORTED_MODES))
    reject = _reject_reason(view)
    if reject:
        return {"conclusion": "FAIL", "reason": reject}
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    cfg = _config_for_view(view)
    before_doc_modified = bool(getattr(doc, "IsModified", False))
    before_state = _capture_crop_state(view)
    basis, cell_req, resolved, original_crop, model_bounds, model_bounds_source, annotation_bounds, canvas_bounds = _compute_bounds(doc, view, cfg)
    base = "{0}_{1}".format(_sanitize_filename(getattr(view, "Name", "view")), _safe_int_id(view.Id))
    modes = ["original", "model_bounds", "canvas_bounds"] if mode == "all" else [mode]
    result = {"paths": [], "view": {"name": getattr(view, "Name", None), "id": _safe_int_id(view.Id), "type": str(getattr(view, "ViewType", None))}, "requested_pixel_size": DEFAULT_FIXED_PIXEL_WIDTH, "resolution_policy": resolution_policy, "target_dpi": target_dpi, "fixed_pixel_width": fixed_pixel_width, "max_pixel_dimension": max_pixel_dimension, "basis": {"origin": list(basis.origin), "right": list(basis.right), "up": list(basis.up), "forward": list(basis.forward)}, "bounds": {"original_crop_uv": _bounds_dict(original_crop), "original_crop_active": before_state.get("CropBoxActive"), "original_crop_visible": before_state.get("CropBoxVisible"), "pre_annotation_model_uv": _bounds_dict(model_bounds), "pre_annotation_model_bounds_source": model_bounds_source, "annotation_uv": _bounds_dict(annotation_bounds), "canvas_uv": _bounds_dict(canvas_bounds), "resolve_view_bounds_result": {k: (_bounds_dict(v) if k.endswith("bounds_uv") or k == "bounds_uv" else v) for k, v in resolved.items() if k != "bounds_uv"}, "resolve_view_bounds_uv": _bounds_dict(resolved.get("bounds_uv"))}, "grid": {"W": int(resolved.get("grid_W", 0) or 0), "H": int(resolved.get("grid_H", 0) or 0), "cell_size_ft_requested": cell_req, "cell_size_ft_effective": float(resolved.get("cell_size_ft_effective", cell_req))}, "state_before": before_state, "document_is_modified_before": before_doc_modified, "exports": {}, "diagnostics": [], "transaction_group": {}, "requires_external_analysis": True}
    _force_close_dynamo_transaction()
    group = TransactionGroup(doc, "VOP Stage A image alignment probe")
    group_status = group.Start()
    group_started = group_status == TransactionStatus.Started
    result["transaction_group"]["start_status"] = str(group_status)
    if not group_started:
        result["rollback_result"] = "not_started"
        result["state_after"] = _capture_crop_state(view)
        result["state_differences"] = [] if result["state_after"] == before_state else [{"before": before_state, "after": result["state_after"]}]
        result["conclusion"] = "FAIL"
        result["diagnostics"].append({"stage": "transaction_group_start", "message": "TransactionGroup.Start returned {0}; no calibration markers, crop changes, or exports were attempted".format(group_status)})
        json_path = os.path.join(out_dir, "{0}.setup_failed.alignment.json".format(base))
        with open(json_path, "w") as f:
            json.dump(result, f, indent=2, sort_keys=True)
        result["paths"].append(json_path)
        return result
    rollback_status = "not_attempted"
    marker_ids = []
    try:
        requested_analysis_bounds = []
        for requested_mode in modes:
            if requested_mode == "original":
                requested_analysis_bounds.append(original_crop if bool(getattr(view, "CropBoxActive", False)) else resolved.get("bounds_uv"))
            elif requested_mode == "model_bounds" and model_bounds is not None:
                requested_analysis_bounds.append(model_bounds)
            elif requested_mode == "canvas_bounds":
                requested_analysis_bounds.append(canvas_bounds)
        marker_bounds = _intersect_bounds(requested_analysis_bounds) or model_bounds or original_crop or resolved.get("bounds_uv") or canvas_bounds
        result["bounds"]["calibration_marker_uv"] = _bounds_dict(marker_bounds)
        result["bounds"]["calibration_marker_bounds_source"] = "intersection_of_requested_export_bounds" if _intersect_bounds(requested_analysis_bounds) is not None else "fallback_bounds_not_common_to_all_requested_exports"
        marker_size = max(cell_req * 2.0, ((_bounds_tuple(marker_bounds)[2] - _bounds_tuple(marker_bounds)[0]) if marker_bounds else 1.0) * 0.01)
        markers, marker_diags = _create_calibration_markers(doc, view, marker_bounds, basis, marker_size) if create_markers else ([], [])
        result["calibration_markers"] = markers
        result["diagnostics"].extend(marker_diags)
        marker_ids = [eid for m in markers for eid in m.get("element_ids", [])]
        analyzed_by_mode = {}
        for run_cfg in selected_runs:
            suffix = _resolution_suffix(run_cfg)
            for m in modes:
                if m == "original":
                    target_bounds = original_crop if bool(getattr(view, "CropBoxActive", False)) else resolved.get("bounds_uv")
                    crop_change = {"changed": False, "reason": "original mode uses existing crop behavior", "analysis_bounds_source": "original_crop" if bool(getattr(view, "CropBoxActive", False)) else "resolve_view_bounds.bounds_uv_inactive_crop"}
                elif m == "model_bounds":
                    target_bounds = model_bounds
                    if target_bounds is None:
                        key = m + "." + suffix
                        json_path = os.path.join(out_dir, "{0}.{1}.{2}.alignment.json".format(base, m, suffix))
                        result["exports"][key] = {"skipped": True, "reason": "model-only bounds unavailable; not substituting annotation-expanded canvas bounds", "target_bounds_uv": None, "images": [], "sequential_export_equality": None, "json_path": json_path}
                        continue
                    crop_change = _set_crop_to_bounds(doc, view, basis, target_bounds, m)
                else:
                    target_bounds = canvas_bounds
                    crop_change = _set_crop_to_bounds(doc, view, basis, target_bounds, m)
                tb = _bounds_tuple(target_bounds); bounds_source = "canvas_bounds" if m == "canvas_bounds" else ("active_model_crop" if before_state.get("CropBoxActive") and m == "original" else ("resolved_model_bounds" if model_bounds_source else "explicit_model_bounds"))
                resolution_report = build_resolution_report(run_cfg.get("policy"), tb[2]-tb[0], tb[3]-tb[1], getattr(view, "Scale", None), run_cfg.get("target_dpi"), bounds_source, run_cfg.get("fixed_pixel_width"), run_cfg.get("max_pixel_dimension"))
                images = []
                for seq in range(1, repetition_count + 1):
                    path = os.path.join(out_dir, "{0}.{1}.{2}.alignment_{3}.tiff".format(base, m, suffix, seq))
                    exported, accepted = _export_tiff(doc, view, path, resolution_report["accepted_width_px"])
                    result["paths"].append(exported)
                    actual_w, actual_h = _actual_tiff_dimensions(exported)
                    analysis = _analyze_image(exported, markers, target_bounds)
                    analysis["effective_pixel_size"] = accepted
                    analysis["resolution"] = _finalize_resolution_report(_resolution_report_for_accepted_width(resolution_report, accepted), actual_w, actual_h)
                    analysis["marker_residual_units"] = {"pixels": "pending_external_analysis", "view_uv_model_units": "pending_external_analysis", "paper_space_inches": "pending_external_analysis"}
                    analysis["pixel_size_equals_actual_width"] = None
                    images.append(analysis)
                sequential_equal = len(images) > 1 and all(images[0].get("sha256") == item.get("sha256") and images[0].get("actual_width") == item.get("actual_width") and images[0].get("actual_height") == item.get("actual_height") for item in images[1:])
                analyzed_by_mode[m] = images[0]
                key = m + "." + suffix
                result["exports"][key] = {"crop_change": crop_change, "target_bounds_uv": _bounds_dict(target_bounds), "images": images, "sequential_export_equality": bool(sequential_equal)}
                json_path = os.path.join(out_dir, "{0}.{1}.{2}.alignment.json".format(base, m, suffix))
                result["exports"][key]["json_path"] = json_path
        model_img = analyzed_by_mode.get("model_bounds") or analyzed_by_mode.get("original")
        res_for_place = None
        if model_img and model_img.get("resolution"):
            res_for_place = model_img["resolution"].get("accepted_pixels_per_model_foot")
        result["model_to_canvas_placement"] = calculate_canvas_placement(model_bounds, canvas_bounds, res_for_place) if res_for_place else _placement(model_bounds, canvas_bounds, model_img)
    finally:
        if group_started:
            try:
                group.RollBack()
                rollback_status = "rolled_back"
            except Exception as ex:
                rollback_status = "rollback_failed: {0}".format(ex)
    result["rollback_result"] = rollback_status
    result["state_after"] = _capture_crop_state(view)
    result["document_is_modified_after"] = bool(getattr(doc, "IsModified", False))
    result["state_differences"] = [] if result["state_after"] == before_state else [{"before": before_state, "after": result["state_after"]}]
    result["calibration_elements_exist_after"] = []
    for eid in marker_ids:
        try:
            from Autodesk.Revit.DB import ElementId
            result["calibration_elements_exist_after"].append({"id": eid, "exists": doc.GetElement(ElementId(int(eid))) is not None})
        except Exception as ex:
            result["calibration_elements_exist_after"].append({"id": eid, "error": str(ex)})
    exported_images = [img for exp in result["exports"].values() for img in exp["images"]]
    ok = rollback_status == "rolled_back" and not result["state_differences"] and not any(x.get("exists") for x in result["calibration_elements_exist_after"])
    result["requires_external_analysis"] = True
    result["evidence_status"] = {"all_have_image_analysis": False, "all_have_marker_analysis": False, "markers_requested": bool(create_markers), "marker_count": len(result.get("calibration_markers", [])), "any_missing_markers": None, "status": "pending_external_analysis"}
    result["conclusion"] = "PASS_PARTIAL" if ok else "FAIL"
    for exp in result["exports"].values():
        with open(exp["json_path"], "w") as f:
            json.dump(result, f, indent=2, sort_keys=True)
        result["paths"].append(exp["json_path"])
    return result


def run_probe(raw_view, output_dir=None, mode="all", create_markers=True,
              resolution_policy=DEFAULT_RESOLUTION_POLICY, target_dpi=DEFAULT_TARGET_DPI,
              fixed_pixel_width=DEFAULT_FIXED_PIXEL_WIDTH,
              max_pixel_dimension=DEFAULT_MAX_PIXEL_DIMENSION,
              resolution_cases="all", repetition_count=2):
    _ensure_contract_import_path(output_dir=output_dir)
    started_at = _probe_contract().utc_now_iso()
    native = _run_native(raw_view, output_dir, mode, create_markers, resolution_policy,
                         target_dpi, fixed_pixel_width, max_pixel_dimension,
                         resolution_cases, repetition_count)
    rollback = native.get("rollback_result")
    differences = native.get("state_differences")
    selected = _probe_contract().select_named(resolution_cases, [_resolution_suffix(item) for item in _resolution_runs(resolution_policy, target_dpi, fixed_pixel_width, max_pixel_dimension)], "resolution case(s)")
    diagnostics = native.get("diagnostics", [])
    return _probe_contract().execution_envelope(
        "stage_a_image_alignment",
        {"mode": mode, "create_markers": bool(create_markers), "resolution_policy": resolution_policy,
         "target_dpi": target_dpi, "fixed_pixel_width": fixed_pixel_width,
         "max_pixel_dimension": max_pixel_dimension, "resolution_cases": selected,
         "repetition_count": int(repetition_count)},
        _probe_contract().view_identity(raw_view), native, native.get("paths", []),
        "succeeded" if rollback == "rolled_back" else ("not_started" if rollback == "not_started" else "failed"),
        "restored" if differences == [] else ("not_checked" if differences is None else "not_restored"), started_at,
        execution_status="completed" if native.get("conclusion") != "FAIL" else "failed",
        errors=diagnostics if native.get("conclusion") == "FAIL" else [],
        warnings=diagnostics if native.get("conclusion") != "FAIL" else [])


def dynamo_main(inputs):
    """Compatibility adapter for the historical Dynamo IN positions."""
    return run_probe(
        inputs[0], inputs[1] if len(inputs) > 1 else None,
        inputs[2] if len(inputs) > 2 else "all",
        inputs[3] if len(inputs) > 3 else True,
        inputs[4] if len(inputs) > 4 else DEFAULT_RESOLUTION_POLICY,
        inputs[5] if len(inputs) > 5 else DEFAULT_TARGET_DPI,
        inputs[6] if len(inputs) > 6 else DEFAULT_FIXED_PIXEL_WIDTH,
        inputs[7] if len(inputs) > 7 else DEFAULT_MAX_PIXEL_DIMENSION,
        inputs[8] if len(inputs) > 8 else "all",
        inputs[9] if len(inputs) > 9 and inputs[9] is not None else 2)


if "IN" in globals():
    try:
        OUT = dynamo_main(IN)  # noqa: F821
    except Exception as ex:
        OUT = {"conclusion": "FAIL", "error": str(ex), "error_type": type(ex).__name__}
