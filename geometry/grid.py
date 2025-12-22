# -*- coding: utf-8 -*-
"""
Grid building and occupancy computation for SSM/VOP exporter.
"""

import math
from core.config import CONFIG

# Track which view-type/crop signatures have already emitted driver-2D debug
DRIVER2D_DEBUG_SIGS = set()

# Revit API imports (will be set by main module)
DOC = None
View = object
ViewType = None
CategoryType = None
ImportInstance = None
FilteredElementCollector = None
BuiltInCategory = None
BuiltInParameter = None
XYZ = None
DSPoint = None
DSPolyCurve = None


def set_revit_context(doc, view_cls, view_type_cls, category_type_cls, import_instance_cls,
                      filtered_element_collector_cls, built_in_category_cls, built_in_parameter_cls,
                      xyz_cls, ds_point_cls, ds_polycurve_cls):
    """Set the Revit API context for this module."""
    global DOC, View, ViewType, CategoryType, ImportInstance, FilteredElementCollector
    global BuiltInCategory, BuiltInParameter, XYZ, DSPoint, DSPolyCurve

    DOC = doc
    View = view_cls
    ViewType = view_type_cls
    CategoryType = category_type_cls
    ImportInstance = import_instance_cls
    FilteredElementCollector = filtered_element_collector_cls
    BuiltInCategory = built_in_category_cls
    BuiltInParameter = built_in_parameter_cls
    XYZ = xyz_cls
    DSPoint = ds_point_cls
    DSPolyCurve = ds_polycurve_cls


# ------------------------------------------------------------
# VIEW SUPPORT
# ------------------------------------------------------------

def _is_supported_2d_view(view):
    if DOC is None or not isinstance(view, View):
        return False

    if ViewType is None:
        return True

    vtype = getattr(view, "ViewType", None)
    if vtype is None:
        return False

    unsupported = [
        ViewType.ThreeD,
        ViewType.DrawingSheet,
        ViewType.Schedule,
    ]
    for attr in ("Walkthrough", "SystemBrowser", "ProjectBrowser", "Report"):
        if hasattr(ViewType, attr):
            unsupported.append(getattr(ViewType, attr))

    if vtype in unsupported:
        return False

    supported = (
        ViewType.FloorPlan,
        ViewType.CeilingPlan,
        ViewType.Section,
        ViewType.Elevation,
        ViewType.Detail,
        ViewType.DraftingView,
    )
    if hasattr(ViewType, "Legend"):
        supported = supported + (ViewType.Legend,)
    if hasattr(ViewType, "EngineeringPlan"):
        supported = supported + (ViewType.EngineeringPlan,)
    if hasattr(ViewType, "AreaPlan"):
        supported = supported + (ViewType.AreaPlan,)

    return vtype in supported


# ------------------------------------------------------------
# GRID: MODEL GEOMETRY EXTENTS
# ------------------------------------------------------------

def _compute_model_geom_extents(view, logger):
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")

    if DOC is None or FilteredElementCollector is None or CategoryType is None:
        return None

    boxes = []

    try:
        col = FilteredElementCollector(DOC, view.Id).WhereElementIsNotElementType()
        for e in col:
            if ImportInstance is not None and isinstance(e, ImportInstance):
                continue

            cat = getattr(e, "Category", None)
            if cat is None:
                continue

            if getattr(cat, "CategoryType", None) != CategoryType.Model:
                continue

            try:
                bic = cat.Id.IntegerValue
            except Exception:
                bic = None

            if BuiltInCategory is not None and bic == int(BuiltInCategory.OST_Cameras):
                continue

            try:
                bb = e.get_BoundingBox(view)
            except Exception:
                bb = None
            if bb is None or bb.Min is None or bb.Max is None:
                continue

            min_x = bb.Min.X
            min_y = bb.Min.Y
            max_x = bb.Max.X
            max_y = bb.Max.Y
            cx = 0.5 * (min_x + max_x)
            cy = 0.5 * (min_y + max_y)

            try:
                eid = getattr(getattr(e, "Id", None), "IntegerValue", None)
            except Exception:
                eid = None
            try:
                cat_name = getattr(cat, "Name", "") or "<NoCat>"
            except Exception:
                cat_name = "<NoCat>"

            boxes.append((cx, cy, min_x, min_y, max_x, max_y, eid, cat_name))

    except Exception as ex:
        logger.warn(
            "Grid: view Id={0} error computing model geometry extents; "
            "falling back to other methods. {1}".format(view_id_val, ex)
        )
        return None

    if not boxes:
        logger.info(
            "Grid: view Id={0} found no model geometry extents".format(view_id_val)
        )
        return None

    n = len(boxes)

    if n <= 10:
        min_x = min(b[2] for b in boxes)
        min_y = min(b[3] for b in boxes)
        max_x = max(b[4] for b in boxes)
        max_y = max(b[5] for b in boxes)
        logger.info(
            "Grid: view Id={0} using model geometry extents (n={1}, no clustering)".format(
                view_id_val, n
            )
        )
        return (min_x, min_y, max_x, max_y)

    sum_cx = sum(b[0] for b in boxes)
    sum_cy = sum(b[1] for b in boxes)
    mean_cx = sum_cx / float(n)
    mean_cy = sum_cy / float(n)

    dist2_list = []
    for idx, (cx, cy, _, _, _, _, _, _) in enumerate(boxes):
        dx = cx - mean_cx
        dy = cy - mean_cy
        d2 = dx * dx + dy * dy
        dist2_list.append((d2, idx))

    dist2_list.sort(key=lambda t: t[0])

    k = max(1, int(0.999 * n))
    keep_indices = set(idx for (_, idx) in dist2_list[:k])

    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    kept = 0
    drivers = {"min_x": None, "min_y": None, "max_x": None, "max_y": None}

    for idx, (cx, cy, bx_min_x, bx_min_y, bx_max_x, bx_max_y, eid, cat_name) in enumerate(boxes):
        if idx not in keep_indices:
            continue
        kept += 1

        if bx_min_x < min_x:
            min_x = bx_min_x
            drivers["min_x"] = (eid, cat_name)
        if bx_min_y < min_y:
            min_y = bx_min_y
            drivers["min_y"] = (eid, cat_name)
        if bx_max_x > max_x:
            max_x = bx_max_x
            drivers["max_x"] = (eid, cat_name)
        if bx_max_y > max_y:
            max_y = bx_max_y
            drivers["max_y"] = (eid, cat_name)

    if kept == 0 or min_x == float("inf"):
        min_x = min(b[2] for b in boxes)
        min_y = min(b[3] for b in boxes)
        max_x = max(b[4] for b in boxes)
        max_y = max(b[5] for b in boxes)
        logger.info(
            "Grid: view Id={0} model geometry clustering removed all candidates; "
            "falling back to raw extents".format(view_id_val)
        )
        return (min_x, min_y, max_x, max_y)

    logger.info(
        "Grid: view Id={0} using clustered model geometry extents "
        "(kept {1}/{2} elements)".format(view_id_val, kept, n)
    )

    return (min_x, min_y, max_x, max_y)

def _compute_drafting_geom_extents(view, crop_box, logger):
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")

    if DOC is None or FilteredElementCollector is None:
        bb_min = crop_box.Min
        bb_max = crop_box.Max
        return (bb_min.X, bb_min.Y, bb_max.X, bb_max.Y)

    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    found_any = False

    try:
        col = FilteredElementCollector(DOC, view.Id).WhereElementIsNotElementType()
        for e in col:
            try:
                bb = e.get_BoundingBox(view)
            except Exception:
                bb = None
            if bb is None or bb.Min is None or bb.Max is None:
                continue

            found_any = True

            if bb.Min.X < min_x:
                min_x = bb.Min.X
            if bb.Min.Y < min_y:
                min_y = bb.Min.Y
            if bb.Max.X > max_x:
                max_x = bb.Max.X
            if bb.Max.Y > max_y:
                max_y = bb.Max.Y

    except Exception as ex:
        logger.warn(
            "Grid: view Id={0} error computing drafting/legend geometry extents; "
            "falling back to crop. {1}".format(view_id_val, ex)
        )
        found_any = False

    if not found_any or min_x == float("inf"):
        bb_min = crop_box.Min
        bb_max = crop_box.Max
        logger.info(
            "Grid: view Id={0} (drafting/legend) found no geometry; using crop extents".format(
                view_id_val
            )
        )
        return (bb_min.X, bb_min.Y, bb_max.X, bb_max.Y)

    logger.info(
        "Grid: view Id={0} (drafting/legend) using view-scoped geometry extents".format(
            view_id_val
        )
    )
    return (min_x, min_y, max_x, max_y)

def _compute_effective_xy_extents(view, crop_box, logger):
    if crop_box is None:
        logger.warn(
            "Grid: view Id={0} has no crop box; using degenerate extents".format(
                getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")
            )
        )
        return (0.0, 0.0, 0.0, 0.0)

    if DOC is None or FilteredElementCollector is None:
        bb_min = crop_box.Min
        bb_max = crop_box.Max
        return (bb_min.X, bb_min.Y, bb_max.X, bb_max.Y)

    vtype = getattr(view, "ViewType", None)
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")

    is_drafting = (vtype == getattr(ViewType, "DraftingView", None))
    is_legend = hasattr(ViewType, "Legend") and (vtype == getattr(ViewType, "Legend"))
    if is_drafting or is_legend:
        return _compute_drafting_geom_extents(view, crop_box, logger)

    try:
        crop_active = bool(getattr(view, "CropBoxActive", True))
    except Exception:
        crop_active = True

    if crop_active:
        bb_min = crop_box.Min
        bb_max = crop_box.Max
        logger.info(
            "Grid: view Id={0} (model view, crop ON) using crop extents for base domain".format(
                view_id_val
            )
        )
        return (bb_min.X, bb_min.Y, bb_max.X, bb_max.Y)

    geom_ext = _compute_model_geom_extents(view, logger)
    if geom_ext is not None:
        min_x, min_y, max_x, max_y = geom_ext
        logger.info(
            "Grid: view Id={0} (model view, crop OFF) using model geometry extents".format(
                view_id_val
            )
        )
        return (min_x, min_y, max_x, max_y)

    bb_min = crop_box.Min
    bb_max = crop_box.Max
    logger.warn(
        "Grid: view Id={0} (model view, crop OFF) could not compute model geometry extents; "
        "falling back to crop".format(view_id_val)
    )
    return (bb_min.X, bb_min.Y, bb_max.X, bb_max.Y)


# ------------------------------------------------------------
# 2D EXTENT DRIVERS (for grid expansion)
# ------------------------------------------------------------

def _is_extent_driver_2d(e):
    cat = getattr(e, "Category", None)
    name = (getattr(cat, "Name", None) or "").lower()

    if "tag" in name:
        return True
    if "dimension" in name:
        return True
    if "text" in name:
        return True

    return False

def _compute_2d_annotation_extents(view, elems2d, logger):
    debug_cfg = CONFIG.get("debug", {})
    enable_debug = bool(debug_cfg.get("enable_driver2d_debug", True))
    log_once = bool(debug_cfg.get("driver2d_log_once_per_signature", False))

    # Get crop box (for both signature + transform)
    try:
        crop_box = getattr(view, "CropBox", None)
    except Exception:
        crop_box = None

    # Build a coarse signature: ViewType + Scale + crop width/height
    sig = None
    if crop_box is not None:
        try:
            bb_min = crop_box.Min
            bb_max = crop_box.Max
            w_x = bb_max.X - bb_min.X
            w_y = bb_max.Y - bb_min.Y
            vtype_str = str(getattr(view, "ViewType", None))
            scale_val = getattr(view, "Scale", None)
            sig = (
                vtype_str,
                int(scale_val) if isinstance(scale_val, int) else None,
                round(w_x, 3),
                round(w_y, 3),
            )
        except Exception:
            sig = None

    do_log = enable_debug
    if enable_debug and log_once and sig is not None:
        global DRIVER2D_DEBUG_SIGS
        if sig in DRIVER2D_DEBUG_SIGS:
            do_log = False
        else:
            DRIVER2D_DEBUG_SIGS.add(sig)

    # ---------------------------------------------------------------
    # Inverse transform: world → crop-local XY
    # ---------------------------------------------------------------
    inv_trf = None
    if crop_box is not None:
        try:
            inv_trf = crop_box.Transform.Inverse
        except Exception:
            inv_trf = None

    def _to_local_xy(pt):
        """Project model-space XYZ → crop-local (x, y) using crop_box."""
        if pt is None:
            return None
        if inv_trf is not None:
            try:
                lp = inv_trf.OfPoint(pt)
                return (lp.X, lp.Y)
            except Exception:
                pass
        # Fallback (rare): treat model XY as local XY
        try:
            return (pt.X, pt.Y)
        except Exception:
            return None

    # ---------------------------------------------------------------
    # Annotation crop + hard cap handling (overlay 2D only)
    # ---------------------------------------------------------------
    # Base crop-local extents (model crop)
    base_min_x = base_min_y = base_max_x = base_max_y = None
    if crop_box is not None:
        try:
            bb_min = crop_box.Min
            bb_max = crop_box.Max
            base_min_x, base_min_y = float(bb_min.X), float(bb_min.Y)
            base_max_x, base_max_y = float(bb_max.X), float(bb_max.Y)
        except Exception:
            base_min_x = base_min_y = base_max_x = base_max_y = None

    # Detect annotation crop active (best-effort)
    ann_crop_active = False
    try:
        ann_crop_active = bool(getattr(view, "AnnotationCropActive", False))
    except Exception:
        ann_crop_active = False
    if not ann_crop_active:
        try:
            p = view.get_Parameter(BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE)
            if p is not None:
                ann_crop_active = bool(p.AsInteger() == 1)
        except Exception:
            pass

    # Hard cap for overlay-driven expansion (in cells)
    cap_cells = None
    try:
        cap_cells = int((CONFIG.get("grid", {}) or {}).get("overlay_expand_cap_cells", 2000))
    except Exception:
        cap_cells = 2000
    if cap_cells < 0:
        cap_cells = 0

    # Cell size in model units (ft) for cap conversion
    cell_size_model = None
    try:
        scale_val = getattr(view, "Scale", None)
        if isinstance(scale_val, int) and scale_val > 0:
            paper_in = float((CONFIG.get("grid", {}) or {}).get("cell_size_paper_in", 0.25))
            cell_size_model = (paper_in / 12.0) * float(scale_val)
    except Exception:
        cell_size_model = None

    # Annotation crop geometry is not reliably accessible in the API across view types / Revit versions.
    # - We DO read AnnotationCropActive reliably (boolean).
    # - When annotation crop is ACTIVE, we approximate its extents as:
    #       model-crop bounds expanded by a fixed printed margin (configurable).
    #   This prevents far-away overlay annotations (hidden by annotation crop) from exploding the grid.
    # - When annotation crop is NOT active, overlay expansion is allowed up to the hard cap envelope.
    ann_min_x = ann_min_y = ann_max_x = ann_max_y = None
    ann_margin_ft = None
    if ann_crop_active and base_min_x is not None:
        try:
            margin_in = float((CONFIG.get("grid", {}) or {}).get("overlay_anno_crop_margin_in", 2.0))
        except Exception:
            margin_in = 2.0
        if margin_in < 0.0:
            margin_in = 0.0
        try:
            scale_val = getattr(view, "Scale", None)
            if isinstance(scale_val, int) and scale_val > 0:
                ann_margin_ft = (margin_in / 12.0) * float(scale_val)
        except Exception:
            ann_margin_ft = None
        if ann_margin_ft is not None:
            ann_min_x = base_min_x - ann_margin_ft
            ann_min_y = base_min_y - ann_margin_ft
            ann_max_x = base_max_x + ann_margin_ft
            ann_max_y = base_max_y + ann_margin_ft

    cap_ft = None
    if cell_size_model is not None:
        try:
            cap_ft = float(cap_cells) * float(cell_size_model)
        except Exception:
            cap_ft = None

    # Allowed expansion envelope around the model crop (crop-local XY):
    # - If annotation crop is active: limit overlay-driven expansion to the proxy annotation-crop bounds.
    # - Else: allow overlay-driven expansion up to the hard cap envelope.
    allow_min_x = allow_min_y = allow_max_x = allow_max_y = None
    if ann_crop_active and ann_min_x is not None:
        allow_min_x, allow_min_y, allow_max_x, allow_max_y = ann_min_x, ann_min_y, ann_max_x, ann_max_y
    elif base_min_x is not None and cap_ft is not None:
        allow_min_x = base_min_x - cap_ft
        allow_min_y = base_min_y - cap_ft
        allow_max_x = base_max_x + cap_ft
        allow_max_y = base_max_y + cap_ft

    # Overlay drivers only: Text / Dimensions / Tags (no crop-clipped 2D)
    driver_elems = [e for e in elems2d if _is_extent_driver_2d(e)]

    if not driver_elems:
        if do_log:
            logger.info(
                "Grid: no driver 2D elements found for view Id={0}".format(
                    getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")
                )
            )
        return None

    samples = []
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")

    try:
        from Autodesk.Revit.DB import Dimension as _Dim
    except Exception:
        _Dim = None

    for e in driver_elems:
        try:
            eid = getattr(getattr(e, "Id", None), "IntegerValue", None)
        except Exception:
            eid = None
        try:
            cat_name = getattr(getattr(e, "Category", None), "Name", None) or "<NoCat>"
        except Exception:
            cat_name = "<NoCat>"
        try:
            type_name = e.GetType().Name
        except Exception:
            type_name = "Element"

        bb_min_x = bb_min_y = bb_max_x = bb_max_y = None

        # Dimensions: use endpoints + text position
        if _Dim is not None and isinstance(e, _Dim):
            pts_local = []
            try:
                curve = getattr(e, "Curve", None)
                if curve is not None:
                    try:
                        p0 = curve.GetEndPoint(0)
                        p1 = curve.GetEndPoint(1)
                        xy0 = _to_local_xy(p0)
                        xy1 = _to_local_xy(p1)
                        if xy0:
                            pts_local.append(xy0)
                        if xy1:
                            pts_local.append(xy1)
                    except Exception:
                        pass
                try:
                    tp = getattr(e, "TextPosition", None)
                    if tp is not None:
                        xy_tp = _to_local_xy(tp)
                        if xy_tp:
                            pts_local.append(xy_tp)
                except Exception:
                    pass
            except Exception:
                pts_local = []

            if pts_local:
                xs = [pt[0] for pt in pts_local]
                ys = [pt[1] for pt in pts_local]
                bb_min_x = min(xs)
                bb_min_y = min(ys)
                bb_max_x = max(xs)
                bb_max_y = max(ys)

        # Fallback: view-specific bounding box
        if bb_min_x is None:
            try:
                bb = e.get_BoundingBox(view)
            except Exception:
                bb = None

            if bb and bb.Min and bb.Max:
                xy_min = _to_local_xy(bb.Min)
                xy_max = _to_local_xy(bb.Max)
                if xy_min and xy_max:
                    bb_min_x, bb_min_y = xy_min
                    bb_max_x, bb_max_y = xy_max

        if bb_min_x is None:
            continue

        if do_log:
            logger.info(
                "Grid: driver2D view Id={0} elemId={1} cat='{2}' type='{3}' "
                "bbX=[{4:.3f},{5:.3f}] bbY=[{6:.3f},{7:.3f}] (crop-local)".format(
                    view_id_val,
                    eid,
                    cat_name,
                    type_name,
                    bb_min_x,
                    bb_max_x,
                    bb_min_y,
                    bb_max_y,
                )
            )

        cx = 0.5 * (bb_min_x + bb_max_x)
        cy = 0.5 * (bb_min_y + bb_max_y)

        samples.append((cx, cy, bb_min_x, bb_min_y, bb_max_x, bb_max_y))

    if not samples:
        return None

    n = len(samples)

    # Simple extents (no clustering needed for small n)
    min_x = min(s[2] for s in samples)
    min_y = min(s[3] for s in samples)
    max_x = max(s[4] for s in samples)
    max_y = max(s[5] for s in samples)

    # Clamp overlay-driven extents to the allowed expansion envelope (hard cap).
    if allow_min_x is not None:
        try:
            min_x = max(min_x, allow_min_x)
            min_y = max(min_y, allow_min_y)
            max_x = min(max_x, allow_max_x)
            max_y = min(max_y, allow_max_y)
        except Exception:
            pass

    # If annotation crop bounds were computed, clamp to them as the final authority for overlay.
    if ann_crop_active and ann_min_x is not None:
        try:
            min_x = max(min_x, ann_min_x)
            min_y = max(min_y, ann_min_y)
            max_x = min(max_x, ann_max_x)
            max_y = min(max_y, ann_max_y)
        except Exception:
            pass

    # If annotation crop is active but we couldn't evaluate its bounds, we set cap_cells=0 above.
    # In that case, treat degenerate extents as "no expansion".
    if base_min_x is not None and (max_x <= min_x or max_y <= min_y):
        return None

    if do_log:
        logger.info(
            "Grid: annotation extents (drivers, n={0}) "
            "X=[{1:.3f},{2:.3f}] Y=[{3:.3f},{4:.3f}] (crop-local)".format(
                n, min_x, max_x, min_y, max_y
            )
        )

    return (min_x, min_y, max_x, max_y)

def _make_rect_polycurve(view, crop_box, min_x, min_y, max_x, max_y):
    if DSPoint is None or DSPolyCurve is None:
        return None

    try:
        T = crop_box.Transform
    except Exception:
        T = None

    pts = []
    corners = [
        (min_x, min_y),
        (max_x, min_y),
        (max_x, max_y),
        (min_x, max_y),
        (min_x, min_y),
    ]

    if T is not None and XYZ is not None:
        for (x, y) in corners:
            try:
                local = XYZ(x, y, 0.0)
                world = T.OfPoint(local)
                pts.append(DSPoint.ByCoordinates(world.X, world.Y, world.Z))
            except Exception:
                pts.append(DSPoint.ByCoordinates(x, y, 0.0))
    else:
        for (x, y) in corners:
            pts.append(DSPoint.ByCoordinates(x, y, 0.0))

    try:
        return DSPolyCurve.ByPoints(pts)
    except Exception:
        return None

def _make_cell_rect_polycurve(view, grid_data, i, j):
    """Build a PolyCurve rectangle for a single grid cell (i,j) in model XY.

    Uses the same crop-box transform as _make_rect_polycurve so the
    preview geometry lands correctly in Dynamo world coordinates.
    """
    if DSPoint is None or DSPolyCurve is None:
        return None

    if not isinstance(grid_data, dict):
        return None

    origin = grid_data.get("origin_model_xy")
    cell_size = grid_data.get("cell_size_model")
    crop_box = grid_data.get("crop_box_model")

    if origin is None or cell_size is None:
        return None

    try:
        ox, oy = origin
        s = float(cell_size)
    except Exception:
        return None

    try:
        i_val = int(i)
        j_val = int(j)
    except Exception:
        return None

    half = 0.5 * s
    cx = ox + i_val * s
    cy = oy + j_val * s

    min_x = cx - half
    max_x = cx + half
    min_y = cy - half
    max_y = cy + half

    try:
        return _make_rect_polycurve(view, crop_box, min_x, min_y, max_x, max_y)
    except Exception:
        return None


# ------------------------------------------------------------
# GRID BUILDING
# ------------------------------------------------------------

def build_grid_for_view(view, config, logger, elems2d=None, clip_data=None, build_clip_volume_for_view_fn=None):
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")
    view_name = getattr(view, "Name", "<no name>")
    logger.info("Grid: building grid for view Id={0}, Name='{1}'".format(view_id_val, view_name))

    cell_size_paper_in = config["grid"]["cell_size_paper_in"]

    grid_data = {
        "cell_size_paper_in": cell_size_paper_in,
        "cell_size_model": None,
        "origin_model_xy": None,
        "crop_box_model": None,
        "crop_xy_min": None,
        "crop_xy_max": None,
        "grid_xy_min": None,
        "grid_xy_max": None,
        "grid_n_i": 0,
        "grid_n_j": 0,
        "valid_cells": [],
        "view_type": str(getattr(view, "ViewType", "Unknown")),
        "crop_rect_geom": None,
        "grid_rect_geom": None,
        "clip_kind": None,
    }

    if DOC is None or not isinstance(view, View):
        logger.warn("Grid: DOC/View unavailable; returning empty grid")
        return grid_data

    if not _is_supported_2d_view(view):
        logger.warn("Grid: view Id={0} not a supported 2D orthographic view; empty grid".format(view_id_val))
        return grid_data

    try:
        crop_box = view.CropBox
    except Exception as ex:
        logger.warn("Grid: view Id={0} error accessing crop box; empty grid. {1}".format(view_id_val, ex))
        return grid_data

    if crop_box is None:
        logger.warn("Grid: view Id={0} has no crop box; empty grid".format(view_id_val))
        return grid_data

    scale = getattr(view, "Scale", None)
    if not isinstance(scale, int) or scale <= 0:
        logger.warn("Grid: view Id={0} has invalid scale '{1}'; empty grid".format(view_id_val, scale))
        return grid_data

    cell_size_model = (cell_size_paper_in / 12.0) * float(scale)
    if cell_size_model <= 0.0:
        logger.warn("Grid: non-positive model cell size for view Id={0}".format(view_id_val))
        return grid_data

    # Stage-2 clip volume (prefer caller-provided to avoid recompute)
    if clip_data is None:
        if build_clip_volume_for_view_fn is None:
            logger.warn("Grid: no clip data and no build_clip_volume_for_view function provided")
            clip = {"kind": None, "is_valid": False}
        else:
            clip = build_clip_volume_for_view_fn(view, config, logger)
    else:
        clip = clip_data

    clip_kind = clip.get("kind", None)
    grid_data["clip_kind"] = clip_kind

    # Helper: point-in-OBB (host model coords). OBB is a dict:
    #   { "center": (x,y,z), "axes": [(..),(..),(..)], "extents": (ex,ey,ez) }
    def _point_in_obb(p, obb):
        try:
            if obb is None:
                return True
            c = obb.get("center")
            axes = obb.get("axes")
            he = obb.get("extents")
            if c is None or axes is None or he is None:
                return True  # fail-open
            dx = p.X - float(c[0]); dy = p.Y - float(c[1]); dz = p.Z - float(c[2])
            # dot(d, axis)
            def _dot(ax):
                return dx * float(ax[0]) + dy * float(ax[1]) + dz * float(ax[2])
            if abs(_dot(axes[0])) > float(he[0]): return False
            if abs(_dot(axes[1])) > float(he[1]): return False
            if abs(_dot(axes[2])) > float(he[2]): return False
            return True
        except Exception:
            return True  # fail-open

    obb_host = clip.get("obb_host", None)
    require_clip = bool(clip.get("is_valid", False)) and clip_kind in ("plan", "vertical") and obb_host is not None

    base_min_x, base_min_y, base_max_x, base_max_y = _compute_effective_xy_extents(view, crop_box, logger)

    # Start from crop-based domain
    min_x = base_min_x
    min_y = base_min_y
    max_x = base_max_x
    max_y = base_max_y

    # Annotation-driven extents (2D drivers)
    ann_ext = None
    if elems2d is not None and len(elems2d) > 0:
        ann_ext = _compute_2d_annotation_extents(view, elems2d, logger)
        if ann_ext is not None:
            ax0, ay0, ax1, ay1 = ann_ext
            min_x = min(min_x, ax0)
            min_y = min(min_y, ay0)
            max_x = max(max_x, ax1)
            max_y = max(max_y, ay1)

    if max_x <= min_x or max_y <= min_y:
        logger.warn("Grid: degenerate effective extents in view Id={0}; empty grid".format(view_id_val))
        return grid_data

    origin_x = min_x + 0.5 * cell_size_model
    origin_y = min_y + 0.5 * cell_size_model

    eps = 1e-9
    if origin_x > max_x + eps or origin_y > max_y + eps:
        logger.warn("Grid: extents smaller than one cell in view Id={0}; empty grid".format(view_id_val))
        return grid_data

    width_x = max_x - origin_x
    width_y = max_y - origin_y

    n_i = int(math.floor(width_x / cell_size_model + eps)) + 1
    n_j = int(math.floor(width_y / cell_size_model + eps)) + 1

    logger.info(
        "Grid: view Id={0} extents X=[{1:.3f},{2:.3f}] Y=[{3:.3f},{4:.3f}], grid {5}x{6} = {7} cells".format(
            view_id_val, min_x, max_x, min_y, max_y, n_i, n_j, n_i * n_j
        )
    )

    max_cells = config["grid"].get("max_cells", None)
    if max_cells is not None:
        total_cells = n_i * n_j
        if total_cells > max_cells:
            logger.warn(
                "Grid: view Id={0} grid {1}x{2} ({3} cells) exceeds max_cells={4}; empty".format(
                    view_id_val, n_i, n_j, total_cells, max_cells
                )
            )
            return grid_data

        # Choose representative center points for Stage-2 clip tests.
    # IMPORTANT: the clip OBB is in HOST MODEL coordinates.
    # Grid XY is in crop-local XY (CropBox space), so we must transform test points.
    rep_z_model = None
    rep_z_local = None

    if clip_kind == "plan":
        z0 = clip.get("z_min", None)
        z1 = clip.get("z_max", None)
        if z0 is not None and z1 is not None:
            rep_z_model = 0.5 * (float(z0) + float(z1))

    elif clip_kind == "vertical":
        # Depth is along CropBox local Z. Prefer explicit local span from clip.
        z0l = clip.get("z0_local", None)
        z1l = clip.get("z1_local", None)
        if z0l is None or z1l is None:
            try:
                z0l = float(crop_box.Min.Z)
                z1l = float(crop_box.Max.Z)
            except Exception:
                z0l = 0.0
                z1l = 0.0
        rep_z_local = 0.5 * (float(z0l) + float(z1l))

    # Fallbacks (fail-open behavior is in _point_in_obb)
    if rep_z_model is None and clip_kind == "plan":
        rep_z_model = 0.0
    if rep_z_local is None and clip_kind == "vertical":
        rep_z_local = 0.0

    valid_cells = []
    for i in range(n_i):
        center_x = origin_x + i * cell_size_model
        if center_x < min_x - eps or center_x > max_x + eps:
            continue
        for j in range(n_j):
            center_y = origin_y + j * cell_size_model
            if center_y < min_y - eps or center_y > max_y + eps:
                continue

            # Stage-2 clip validity: require cell center inside VOP clip volume (plans/vertical)
            if require_clip:
                # Build a host-model test point from crop-local XY.
                if clip_kind == "plan":
                    try:
                        p0 = crop_box.Transform.OfPoint(XYZ(center_x, center_y, 0.0))
                        p = XYZ(p0.X, p0.Y, rep_z_model)
                    except Exception:
                        p = XYZ(center_x, center_y, rep_z_model)
                else:
                    # vertical
                    try:
                        p = crop_box.Transform.OfPoint(XYZ(center_x, center_y, rep_z_local))
                    except Exception:
                        p = XYZ(center_x, center_y, rep_z_local)

                if not _point_in_obb(p, obb_host):
                    continue

            valid_cells.append((i, j))

    grid_data["cell_size_model"] = cell_size_model
    grid_data["origin_model_xy"] = (origin_x, origin_y)
    grid_data["crop_box_model"] = crop_box
    grid_data["crop_xy_min"] = (base_min_x, base_min_y)
    grid_data["crop_xy_max"] = (base_max_x, base_max_y)
    grid_data["grid_xy_min"] = (min_x, min_y)
    grid_data["grid_xy_max"] = (max_x, max_y)
    grid_data["grid_n_i"] = n_i
    grid_data["grid_n_j"] = n_j
    grid_data["valid_cells"] = valid_cells

    try:
        grid_data["crop_rect_geom"] = _make_rect_polycurve(view, crop_box, base_min_x, base_min_y, base_max_x, base_max_y)
        grid_data["grid_rect_geom"] = _make_rect_polycurve(view, crop_box, min_x, min_y, max_x, max_y)
    except Exception as ex:
        logger.warn("Grid: failed to build Dynamo rectangles for view Id={0}: {1}".format(view_id_val, ex))

    logger.info(
        "Grid-debug: view Id={0} cropXY={1}→{2}, annXY={3}→{4}, gridXY={5}→{6}".format(
            view_id_val,
            (base_min_x, base_min_y),
            (base_max_x, base_max_y),
            (ann_ext if ann_ext is not None else None),
            None if ann_ext is None else (ann_ext[2], ann_ext[3]),
            (min_x, min_y),
            (max_x, max_y),
        )
    )
    logger.info(
        "Grid: view Id={0} -> cell_size_model={1:.6f} ft, grid {2}x{3}, {4} valid cell(s)".format(
            view_id_val, cell_size_model, n_i, n_j, len(valid_cells)
        )
    )

    return grid_data


# ------------------------------------------------------------
# OCCUPANCY
# ------------------------------------------------------------

def compute_occupancy(grid_data, raster_data, config, logger):
    """
    Compute per-cell occupancy for a view, independent of annotation buckets.

    Inputs
    ------
    grid_data : dict
        Includes 'valid_cells' (list of (i,j) cell indices) and cell size info.
    raster_data : dict
        Expected keys:
            - "cells_3d": {cell -> any payload}
            - "cells_2d": {cell -> any payload}
        Only the keys (cell ids) are used here.
    config : dict
        Uses config["occupancy"]["code_3d_only"],
             config["occupancy"]["code_2d_only"],
             config["occupancy"]["code_2d_over_3d"].
    logger : logger-like
        For INFO logging.

    Returns
    -------
    dict with:
        - "occupancy_map": {cell -> code}
        - "code_3d_only", "code_2d_only", "code_2d_over_3d"
        - "diagnostics": {
              "num_cells_total",
              "num_cells_3d_only",
              "num_cells_2d_only",
              "num_cells_2d_over_3d",
              "num_cells_3d_layer",
              "num_cells_2d_layer",
          }

    Notes
    -----
    This is intentionally agnostic to annotation categories
    (TEXT / TAG / DIM / DETAIL / LINES / REGION / OTHER).
    Those are handled separately via anno_cells[…].
    """

    logger.info("Occupancy: computing final occupancy from raster layers")

    raster_data = raster_data or {}
    cells_3d = raster_data.get("cells_3d") or {}
    cells_2d = raster_data.get("cells_2d") or {}

    code_3d = config["occupancy"]["code_3d_only"]
    code_2d = config["occupancy"]["code_2d_only"]
    code_2d_over_3d = config["occupancy"]["code_2d_over_3d"]

    occupancy_map = {}

    # First pass: mark 3D-only cells (or 2D-over-3D if 2D was already set)
    for cell in cells_3d.keys():
        existing = occupancy_map.get(cell)
        if existing is None:
            occupancy_map[cell] = code_3d
        elif existing == code_2d:
            occupancy_map[cell] = code_2d_over_3d

    # Second pass: mark 2D-only cells (or 2D-over-3D if 3D was already set)
    for cell in cells_2d.keys():
        existing = occupancy_map.get(cell)
        if existing is None:
            occupancy_map[cell] = code_2d
        elif existing == code_3d:
            occupancy_map[cell] = code_2d_over_3d

    n_total = len(occupancy_map)
    n_3d_only = 0
    n_2d_only = 0
    n_2d_over_3d = 0

    for code in occupancy_map.values():
        if code == code_3d:
            n_3d_only += 1
        elif code == code_2d:
            n_2d_only += 1
        elif code == code_2d_over_3d:
            n_2d_over_3d += 1

    logger.info(
        "Occupancy: {0} cells total ({1} 3D-only, {2} 2D-only, {3} 2D-over-3D)".format(
            n_total, n_3d_only, n_2d_only, n_2d_over_3d
        )
    )

    return {
        "occupancy_map": occupancy_map,
        "code_3d_only": code_3d,
        "code_2d_only": code_2d,
        "code_2d_over_3d": code_2d_over_3d,
        "diagnostics": {
            "num_cells_total": n_total,
            "num_cells_3d_only": n_3d_only,
            "num_cells_2d_only": n_2d_only,
            "num_cells_2d_over_3d": n_2d_over_3d,
            "num_cells_3d_layer": len(cells_3d),
            "num_cells_2d_layer": len(cells_2d),
        },
    }
