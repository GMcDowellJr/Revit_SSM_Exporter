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
import importlib.util
import json
import math
import os
import re
import sys
import time

REQUESTED_PIXEL_SIZE = 1600
SUPPORTED_MODES = ("original", "model_bounds", "canvas_bounds", "all")
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


def _solve_3x3(matrix, vector):
    """Gaussian elimination with partial pivoting for a 3x3 linear system.

    Returns None if the matrix is singular (e.g. markers are collinear in UV
    space, which cannot happen with the standard 5-marker layout but is
    checked rather than assumed).
    """
    m = [row[:] for row in matrix]
    v = vector[:]
    n = 3
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        v[col], v[pivot] = v[pivot], v[col]
        for r in range(col + 1, n):
            factor = m[r][col] / m[col][col]
            for c in range(col, n):
                m[r][c] -= factor * m[col][c]
            v[r] -= factor * v[col]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = v[r] - sum(m[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / m[r][r]
    return x


def _affine_fit(detections):
    """Independent least-squares affine (6-DOF) fit from expected_uv -> centroid_px.

    px_x = a*u + b*v + c
    px_y = d*u + e*v + f

    This is a second, independently-computed residual alongside the existing
    naive-equation ``predicted_px_center_equation`` / ``residual_px`` fields --
    it does not replace them. Uses only markers with an exact color match
    (``found_exact``). ``centroid_px`` is populated even on a miss, from the
    single nearest-colored pixel found anywhere in the image (see
    ``_analyze_image``'s ``best`` fallback) -- on a blank or markerless export
    that fallback centroid is essentially an arbitrary background pixel, and
    fitting against it would let the affine fit report a plausible-looking
    near-zero residual that contradicts the sibling ``missing_markers``
    evidence. Requires at least 4 exact-match correspondences for a
    well-posed fit (6 unknowns, 2 equations per marker).
    """
    usable = [d for d in detections if d.get("found_exact") and d.get("centroid_px") is not None]
    if len(usable) < 4:
        return {
            "available": False,
            "reason": "fewer than 4 markers with an exact color-match centroid ({0} available, "
                      "{1} total detections including nearest-color fallbacks); an affine fit "
                      "needs >=4 correspondences for 6 unknowns and fallback centroids are excluded "
                      "because they are not reliable marker positions".format(len(usable), len(detections)),
            "markers_used": len(usable),
        }
    rows = [[d["expected_uv"][0], d["expected_uv"][1], 1.0] for d in usable]
    bx = [d["centroid_px"][0] for d in usable]
    by = [d["centroid_px"][1] for d in usable]
    ata = [[0.0] * 3 for _ in range(3)]
    atbx = [0.0] * 3
    atby = [0.0] * 3
    for row, tx, ty in zip(rows, bx, by):
        for r in range(3):
            for c in range(3):
                ata[r][c] += row[r] * row[c]
            atbx[r] += row[r] * tx
            atby[r] += row[r] * ty
    coeffs_x = _solve_3x3(ata, atbx)
    coeffs_y = _solve_3x3(ata, atby)
    if coeffs_x is None or coeffs_y is None:
        return {"available": False, "reason": "normal-equation matrix is singular (markers collinear in UV space)", "markers_used": len(usable)}
    a, b, c = coeffs_x
    d_, e, f = coeffs_y
    per_marker = []
    magnitudes = []
    for det in usable:
        u, v = det["expected_uv"]
        px = a * u + b * v + c
        py = d_ * u + e * v + f
        cx, cy = det["centroid_px"]
        rx, ry = cx - px, cy - py
        mag = math.sqrt(rx * rx + ry * ry)
        magnitudes.append(mag)
        per_marker.append({"name": det["name"], "predicted_px_affine_fit": [px, py], "residual_px": [rx, ry], "residual_magnitude_px": mag})
    return {
        "available": True,
        "method": "least_squares_affine_6dof",
        "form": "px_x = a*u + b*v + c; px_y = d*u + e*v + f",
        "markers_used": len(usable),
        "matrix": {"a": a, "b": b, "c": c, "d": d_, "e": e, "f": f},
        "per_marker_residual_px": per_marker,
        "max_residual_px": max(magnitudes) if magnitudes else None,
    }


def _analyze_image(path, markers, bounds):
    res = {"path": path, "file_size": os.path.getsize(path), "sha256": _sha256(path), "pillow_available": False}
    if importlib.util.find_spec("PIL") is None:
        res["diagnostic"] = "Pillow unavailable; image dimensions/orientation require manual review"
        return res
    from PIL import Image
    img = Image.open(path).convert("RGB")
    w, h = img.size
    res.update({"pillow_available": True, "actual_width": int(w), "actual_height": int(h)})
    colors = img.getcolors(maxcolors=w * h + 1)
    res["distinct_colors"] = None if colors is None else len(colors)
    bg = max(colors, key=lambda c: c[0])[1] if colors else img.getpixel((0, 0))
    pix = img.load()
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            if pix[x, y] != bg:
                xs.append(x); ys.append(y)
    content = [min(xs), min(ys), max(xs), max(ys)] if xs else None
    res["background_rgb"] = list(bg)
    res["content_rect_px"] = content
    cw = (content[2] - content[0] + 1) if content else w
    ch = (content[3] - content[1] + 1) if content else h
    cx0 = content[0] if content else 0
    cy0 = content[1] if content else 0
    bt = _bounds_tuple(bounds)
    detections = []
    for m in markers or []:
        target = tuple(int(v) for v in m["rgb"])
        pts = []
        best = None
        best_d = 10 ** 9
        for y in range(h):
            for x in range(w):
                p = pix[x, y]
                d = sum((int(p[i]) - target[i]) ** 2 for i in range(3))
                if d == 0:
                    pts.append((x, y))
                if d < best_d:
                    best_d, best = d, (x, y, p)
        found = bool(pts)
        use = pts if found else [(best[0], best[1])] if best else []
        centroid = [sum(p[0] for p in use) / float(len(use)), sum(p[1] for p in use) / float(len(use))] if use else None
        residual = None
        predicted = None
        if centroid and bt:
            u0, v0, u1, v1 = bt
            predicted = [((m["uv"][0] - u0) / (u1 - u0)) * float(w) - 0.5, ((v1 - m["uv"][1]) / (v1 - v0)) * float(h) - 0.5]
            residual = [centroid[0] - predicted[0], centroid[1] - predicted[1]]
        detections.append({"name": m["name"], "expected_uv": m["uv"], "rgb": m["rgb"], "found_exact": found, "centroid_px": centroid, "nearest_rgb": (list(best[2]) if best else None), "nearest_distance_sq": int(best_d) if best else None, "predicted_px_center_equation": predicted, "residual_px": residual})
    res["markers"] = detections
    res["affine_fit_residual"] = _affine_fit(detections)
    res["marker_analysis_available"] = bool(detections)
    res["missing_markers"] = [d["name"] for d in detections if not d["found_exact"]]
    res["padding_px"] = {"left": cx0, "top": cy0, "right": (w - content[2] - 1) if content else 0, "bottom": (h - content[3] - 1) if content else 0}
    if bt:
        res["tested_mapping"] = "u = u_min + (x + 0.5) * UV_width / image_width; v = v_max - (y + 0.5) * UV_height / image_height"
        res["mapping_frame"] = "full_exported_image_frame_not_content_rect"
        res["x_axis_direction"] = "+U to the right" if bt[2] > bt[0] else "unknown"
        res["y_axis_direction"] = "-V downward / +V upward" if bt[3] > bt[1] else "unknown"
        if detections and all(d["residual_px"] for d in detections):
            res["max_marker_residual_px"] = max(math.sqrt(d["residual_px"][0] ** 2 + d["residual_px"][1] ** 2) for d in detections)
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


def _run():
    out_dir = IN[1] if len(IN) > 1 and IN[1] else os.path.join(os.path.expanduser("~"), "Desktop")  # noqa: F821
    _ensure_repo_on_path(output_dir=out_dir)
    _ensure_revit_api_reference()
    from Autodesk.Revit.DB import TransactionGroup, TransactionStatus
    doc = _document_manager_doc()
    view = _unwrap(IN[0])  # noqa: F821
    mode = (IN[2] if len(IN) > 2 and IN[2] else "all").strip().lower()  # noqa: F821
    create_markers = bool(IN[3]) if len(IN) > 3 and IN[3] is not None else True  # noqa: F821
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
    result = {"paths": [], "view": {"name": getattr(view, "Name", None), "id": _safe_int_id(view.Id), "type": str(getattr(view, "ViewType", None))}, "requested_pixel_size": REQUESTED_PIXEL_SIZE, "basis": {"origin": list(basis.origin), "right": list(basis.right), "up": list(basis.up), "forward": list(basis.forward)}, "bounds": {"original_crop_uv": _bounds_dict(original_crop), "original_crop_active": before_state.get("CropBoxActive"), "original_crop_visible": before_state.get("CropBoxVisible"), "pre_annotation_model_uv": _bounds_dict(model_bounds), "pre_annotation_model_bounds_source": model_bounds_source, "annotation_uv": _bounds_dict(annotation_bounds), "canvas_uv": _bounds_dict(canvas_bounds), "resolve_view_bounds_result": {k: (_bounds_dict(v) if k.endswith("bounds_uv") or k == "bounds_uv" else v) for k, v in resolved.items() if k != "bounds_uv"}, "resolve_view_bounds_uv": _bounds_dict(resolved.get("bounds_uv"))}, "grid": {"W": int(resolved.get("grid_W", 0) or 0), "H": int(resolved.get("grid_H", 0) or 0), "cell_size_ft_requested": cell_req, "cell_size_ft_effective": float(resolved.get("cell_size_ft_effective", cell_req))}, "state_before": before_state, "document_is_modified_before": before_doc_modified, "exports": {}, "diagnostics": [], "transaction_group": {}}
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
        for m in modes:
            if m == "original":
                target_bounds = original_crop if bool(getattr(view, "CropBoxActive", False)) else resolved.get("bounds_uv")
                crop_change = {"changed": False, "reason": "original mode uses existing crop behavior", "analysis_bounds_source": "original_crop" if bool(getattr(view, "CropBoxActive", False)) else "resolve_view_bounds.bounds_uv_inactive_crop"}
            elif m == "model_bounds":
                target_bounds = model_bounds
                if target_bounds is None:
                    json_path = os.path.join(out_dir, "{0}.{1}.alignment.json".format(base, m))
                    result["exports"][m] = {
                        "skipped": True,
                        "reason": "model-only bounds unavailable; not substituting annotation-expanded canvas bounds",
                        "target_bounds_uv": None,
                        "images": [],
                        "sequential_export_equality": None,
                        "json_path": json_path,
                    }
                    continue
                crop_change = _set_crop_to_bounds(doc, view, basis, target_bounds, m)
            else:
                target_bounds = canvas_bounds
                crop_change = _set_crop_to_bounds(doc, view, basis, target_bounds, m)
            images = []
            for seq in (1, 2):
                path = os.path.join(out_dir, "{0}.{1}.alignment_{2}.tiff".format(base, m, seq))
                exported, accepted = _export_tiff(doc, view, path, REQUESTED_PIXEL_SIZE)
                result["paths"].append(exported)
                analysis = _analyze_image(exported, markers, target_bounds)
                analysis["effective_pixel_size"] = accepted
                analysis["pixel_size_equals_actual_width"] = (analysis.get("actual_width") == accepted)
                images.append(analysis)
            sequential_equal = images[0].get("sha256") == images[1].get("sha256") and images[0].get("actual_width") == images[1].get("actual_width") and images[0].get("actual_height") == images[1].get("actual_height")
            analyzed_by_mode[m] = images[0]
            result["exports"][m] = {"crop_change": crop_change, "target_bounds_uv": _bounds_dict(target_bounds), "images": images, "sequential_export_equality": bool(sequential_equal)}
            json_path = os.path.join(out_dir, "{0}.{1}.alignment.json".format(base, m))
            result["exports"][m]["json_path"] = json_path
        model_img = analyzed_by_mode.get("model_bounds") or analyzed_by_mode.get("original")
        result["model_to_canvas_placement"] = _placement(model_bounds, canvas_bounds, model_img)
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
    any_missing = any(img.get("missing_markers") for img in exported_images)
    all_have_image_analysis = bool(exported_images) and all(img.get("pillow_available") for img in exported_images)
    all_have_marker_analysis = bool(create_markers) and bool(result.get("calibration_markers")) and all(img.get("marker_analysis_available") for img in exported_images)
    ok = rollback_status == "rolled_back" and not result["state_differences"] and not any(x.get("exists") for x in result["calibration_elements_exist_after"])
    result["evidence_status"] = {"all_have_image_analysis": all_have_image_analysis, "all_have_marker_analysis": all_have_marker_analysis, "markers_requested": bool(create_markers), "marker_count": len(result.get("calibration_markers", [])), "any_missing_markers": bool(any_missing)}
    result["conclusion"] = "PASS" if ok and all_have_image_analysis and all_have_marker_analysis and not any_missing else ("FAIL" if not ok else "INCONCLUSIVE")
    for exp in result["exports"].values():
        with open(exp["json_path"], "w") as f:
            json.dump(result, f, indent=2, sort_keys=True)
        result["paths"].append(exp["json_path"])
    return result


try:
    OUT = _run()  # noqa: F821
except Exception as ex:
    OUT = {"conclusion": "FAIL", "error": str(ex), "error_type": type(ex).__name__}  # noqa: F821
