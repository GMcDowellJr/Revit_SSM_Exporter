"""
View basis extraction for VOP interwoven pipeline.

Provides view coordinate system (O, R, U, F) extraction from Revit views
for transforming between model coordinates and view-local UV space.
"""


class ViewBasis:
    """View coordinate system with origin and basis vectors.

    Attributes:
        origin: View origin point (O) in model coordinates
        right: Right vector (R) - view X axis
        up: Up vector (U) - view Y axis
        forward: Forward/view direction vector (F) - view Z axis (into screen)

    Example:
        >>> # Plan view looking down at Z=0
        >>> basis = ViewBasis(
        ...     origin=(0, 0, 0),
        ...     right=(1, 0, 0),
        ...     up=(0, 1, 0),
        ...     forward=(0, 0, -1)
        ... )
        >>> basis.is_plan_like()
        True
    """

    def __init__(self, origin, right, up, forward):
        self.origin = tuple(origin)
        self.right = tuple(right)
        self.up = tuple(up)
        self.forward = tuple(forward)

    def is_plan_like(self):
        """Check if view is plan-like (looking down Z axis).

        Returns:
            True if forward vector is mostly vertical (|F.Z| > 0.9)
        """
        return abs(self.forward[2]) > 0.9

    def is_elevation_like(self):
        """Check if view is elevation-like (horizontal view direction).

        Returns:
            True if forward vector is mostly horizontal (|F.Z| < 0.1)
        """
        return abs(self.forward[2]) < 0.1

    def transform_to_view_uv(self, point_model):
        """Transform model-space point to view-local UV coordinates.

        Args:
            point_model: (x, y, z) in model coordinates

        Returns:
            (u, v) in view-local coordinates (aligned with view right/up axes)

        Example:
            >>> basis = ViewBasis((0,0,0), (1,0,0), (0,1,0), (0,0,-1))
            >>> basis.transform_to_view_uv((5, 10, 0))
            (5.0, 10.0)
        """
        # Translate to view origin
        dx = point_model[0] - self.origin[0]
        dy = point_model[1] - self.origin[1]
        dz = point_model[2] - self.origin[2]

        # Project onto right and up axes
        u = dx * self.right[0] + dy * self.right[1] + dz * self.right[2]
        v = dx * self.up[0] + dy * self.up[1] + dz * self.up[2]

        return (u, v)

    def transform_to_view_uvw(self, point_model):
        """Transform model-space point to view-local UVW coordinates.

        Args:
            point_model: (x, y, z) in model coordinates

        Returns:
            (u, v, w) where w is depth along view direction

        Example:
            >>> basis = ViewBasis((0,0,0), (1,0,0), (0,1,0), (0,0,-1))
            >>> basis.transform_to_view_uvw((5, 10, 3))
            (5.0, 10.0, -3.0)
        """
        dx = point_model[0] - self.origin[0]
        dy = point_model[1] - self.origin[1]
        dz = point_model[2] - self.origin[2]

        u = dx * self.right[0] + dy * self.right[1] + dz * self.right[2]
        v = dx * self.up[0] + dy * self.up[1] + dz * self.up[2]
        w = dx * self.forward[0] + dy * self.forward[1] + dz * self.forward[2]

        return (u, v, w)
    
    def world_to_view_local(self, p):
        """Back-compat helper: accept XYZ or tuple, return view-local (u, v, w)."""
        try:
            # Autodesk.Revit.DB.XYZ
            x, y, z = p.X, p.Y, p.Z
        except Exception:
            # tuple/list
            x, y, z = p[0], p[1], p[2]
        return self.transform_to_view_uvw((x, y, z))

    def __repr__(self):
        return f"ViewBasis(origin={self.origin}, right={self.right}, up={self.up}, forward={self.forward})"


def world_to_view(pt, vb):
    """Transform world point to view coordinates (standalone helper).

    Args:
        pt: (x, y, z) point in world coordinates
        vb: ViewBasis object

    Returns:
        (u, v, w) in view-local coordinates (w is depth)

    Example:
        >>> vb = ViewBasis((0,0,0), (1,0,0), (0,1,0), (0,0,-1))
        >>> world_to_view((5, 10, 3), vb)
        (5.0, 10.0, -3.0)
    """
    return vb.transform_to_view_uvw(pt)


def make_view_basis(view, diag=None):
    """Extract view basis from Revit View."""
    from Autodesk.Revit.DB import ViewType

    view_id = None
    try:
        view_id = getattr(getattr(view, "Id", None), "IntegerValue", None)
    except Exception:
        view_id = None

    try:
        origin = view.Origin
        right = view.RightDirection
        up = view.UpDirection

        # Forward must point INTO the view.
        # Revit's ViewDirection points from the view towards the model; for "depth into the view"
        # and front-to-back sorting, we want the opposite sign.
        try:
            vd = view.ViewDirection.Normalize()
        except Exception:
            vd = right.CrossProduct(up).Normalize()

        forward = vd.Negate()

        # Plan views: origin on cut plane
        origin_z = origin.Z
        try:
            if view.ViewType in [ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan]:
                vr = view.GetViewRange()
                if vr is not None:
                    cut_level_id = vr.GetLevelId(0)
                    cut_offset = vr.GetOffset(0)
                    cut_level = view.Document.GetElement(cut_level_id)
                    if cut_level is not None:
                        origin_z = cut_level.Elevation + cut_offset
        except Exception as e:
            # This is a correctness degradation (depth origin changes). Record it once.
            try:
                if diag is not None:
                    diag.warn(
                        phase="view_basis",
                        callsite="make_view_basis",
                        message="Could not get plan cut plane; using view.Origin.Z",
                        view_id=view_id,
                        extra={"exc_type": type(e).__name__, "exc": str(e)},
                    )
            except Exception:
                pass

        return ViewBasis(
            origin=(origin.X, origin.Y, origin_z),
            right=(right.X, right.Y, right.Z),
            up=(up.X, up.Y, up.Z),
            forward=(forward.X, forward.Y, forward.Z),
        )

    except Exception as e:
        # Basis extraction failing is severe: we are returning identity fallback.
        try:
            if diag is not None:
                diag.error(
                    phase="view_basis",
                    callsite="make_view_basis",
                    message="make_view_basis failed; falling back to identity basis",
                    exc=e,
                    view_id=view_id,
                )
        except Exception:
            pass

        return ViewBasis(
            origin=(0.0, 0.0, 0.0),
            right=(1.0, 0.0, 0.0),
            up=(0.0, 1.0, 0.0),
            forward=(0.0, 0.0, -1.0),
        )

def resolve_view_w_volume(view, vb, cfg, diag=None):
    """Resolve view-space W volume [W0, Wmax] for the host view.

    Unification rule:
        Use linked_documents._build_clip_volume(view, cfg) as the single
        canonical definition of the host view volume (already used for links).
        Project its corners into vb to get a W interval.

    Returns:
        (W0, Wmax, meta)
        - W0/Wmax are floats when available; otherwise (None, None, meta)
        - meta includes clip kind/depth_mode for diagnostics
    """
    try:
        from .linked_documents import _build_clip_volume
    except Exception as e:
        if diag is not None:
            try:
                diag.warn(
                    phase="view_basis",
                    callsite="resolve_view_w_volume",
                    message="Could not import linked_documents._build_clip_volume; no view volume",
                    view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                    extra={"exc_type": type(e).__name__, "exc": str(e)},
                )
            except Exception:
                pass
        return (None, None, {"is_valid": False, "reason": "import_failed"})

    clip = None
    try:
        clip = _build_clip_volume(view, cfg)
    except Exception as e:
        if diag is not None:
            try:
                diag.warn(
                    phase="view_basis",
                    callsite="resolve_view_w_volume",
                    message="Failed to build clip volume; no view volume",
                    view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                    extra={"exc_type": type(e).__name__, "exc": str(e)},
                )
            except Exception:
                pass
        return (None, None, {"is_valid": False, "reason": "build_failed"})

    if not clip or not clip.get("is_valid", False):
        return (None, None, {"is_valid": False, "reason": "clip_invalid", "clip": clip})

    corners = clip.get("corners_host") or []
    if len(corners) < 8:
        return (None, None, {"is_valid": False, "reason": "corners_missing", "clip": clip})

    ws = []
    for p in corners:
        try:
            u, v, w = vb.world_to_view_local(p)
            ws.append(float(w))
        except Exception:
            continue

    if not ws:
        return (None, None, {"is_valid": False, "reason": "projection_failed", "clip": clip})

    w0 = min(ws)
    wmax = max(ws)

    meta = {
        "is_valid": True,
        "kind": clip.get("kind"),
        "depth_mode": clip.get("depth_mode"),
        "z_min": clip.get("z_min"),
        "z_max": clip.get("z_max"),
    }
    return (w0, wmax, meta)


def xy_bounds_from_crop_box_all_corners(view, basis, buffer=0.0):
    """Compute XY bounds from view crop box (all 8 corners method).

    Notes on API correctness:
        Revit's BoundingBoxXYZ (e.g. View.CropBox) may have a non-identity Transform.
        Min/Max are expressed in the crop box's local coordinates, so we must transform
        the 8 corners to model coordinates before projecting into view UV space.

    Args:
        view: Revit View object
        basis: ViewBasis for coordinate transformation
        buffer: Additional margin to add (in model units)

    Returns:
        Bounds2D in view-local XY coordinates
    """
    from ..core.math_utils import Bounds2D

    try:
        crop_box = view.CropBox
        if crop_box is None:
            raise AttributeError("View has no CropBox")

        T = getattr(crop_box, "Transform", None)

        min_pt = crop_box.Min
        max_pt = crop_box.Max

        # In Revit runtime, XYZ should always be available.
        from Autodesk.Revit.DB import XYZ

        corners_local = [
            XYZ(min_pt.X, min_pt.Y, min_pt.Z),
            XYZ(max_pt.X, min_pt.Y, min_pt.Z),
            XYZ(min_pt.X, max_pt.Y, min_pt.Z),
            XYZ(max_pt.X, max_pt.Y, min_pt.Z),
            XYZ(min_pt.X, min_pt.Y, max_pt.Z),
            XYZ(max_pt.X, min_pt.Y, max_pt.Z),
            XYZ(min_pt.X, max_pt.Y, max_pt.Z),
            XYZ(max_pt.X, max_pt.Y, max_pt.Z),
        ]

        if T is not None:
            corners_world_xyz = [T.OfPoint(p) for p in corners_local]
        else:
            corners_world_xyz = corners_local

        # Always project using view-local basis (pass tuples if your basis expects tuples)
        corners_uv = [
            basis.transform_to_view_uv((p.X, p.Y, p.Z))
            for p in corners_world_xyz
        ]

        u_coords = [uv[0] for uv in corners_uv]
        v_coords = [uv[1] for uv in corners_uv]

        return Bounds2D(
            min(u_coords) - buffer,
            min(v_coords) - buffer,
            max(u_coords) + buffer,
            max(v_coords) + buffer,
        )

    except AttributeError:
        # Fallback for views without crop box
        return Bounds2D(-100.0 - buffer, -100.0 - buffer, 100.0 + buffer, 100.0 + buffer)

def xy_bounds_effective(doc, view, basis, buffer=0.0, diag=None):
    """Compute EFFECTIVE view bounds in view-local UV.

    Behaves as if the view had a usable crop box:
    - If CropBox is active & available -> use CropBox (correctly applying CropBox.Transform)
    - Otherwise -> compute bounds from visible element extents (synthetic crop)
      This is required for DraftingView (no CropBox at all).

    Args:
        doc: Revit Document
        view: Revit View
        basis: ViewBasis
        buffer: margin in feet

    Returns:
        Bounds2D in view-local UV coordinates
    """
    # Prefer crop box when the view supports it and crop is active
    try:
        crop_active = bool(view.CropBoxActive)
    except Exception:
        crop_active = False

    if crop_active:
        try:
            return xy_bounds_from_crop_box_all_corners(view, basis, buffer=buffer)
        except Exception:
            # fall through to synthetic extents
            pass

    # Fallback: extents derived from what is in the view (drafting-safe)
    return synthetic_bounds_from_visible_extents(doc, view, basis, buffer=buffer)

def synthetic_bounds_from_visible_extents(
    doc,
    view,
    basis,
    buffer=0.0,
    diag=None,
    max_elements=None,
    time_budget_s=None,
    time_fn=None,
):
    """Compute synthetic bounds from element extents in a view (crop-off / no-crop views).

    Budget semantics:
        - If max_elements is exceeded OR time_budget_s elapses, the scan stops early.
        - Any early-stop yields confidence='low' and an explicit diagnostic (no silent degradation).
        - If early-stop occurs before any bounds are found, returns default bounds.

    Returns:
        dict with:
            bounds_uv: Bounds2D
            confidence: 'med' | 'low'
            budget: dict with scan counters + trigger info
    """
    from ..core.math_utils import Bounds2D
    from Autodesk.Revit.DB import FilteredElementCollector, ViewDrafting, XYZ
    import time as _time

    if time_fn is None:
        time_fn = _time.time

    view_id = None
    try:
        view_id = getattr(getattr(view, "Id", None), "IntegerValue", None)
    except Exception:
        view_id = None

    is_drafting = isinstance(view, ViewDrafting)

    # Default fallback bounds (200ft x 200ft centered at origin)
    default_bounds = Bounds2D(
        -100.0 - buffer,
        -100.0 - buffer,
        100.0 + buffer,
        100.0 + buffer,
    )

    min_u = float("inf")
    min_v = float("inf")
    max_u = float("-inf")
    max_v = float("-inf")
    found = 0

    # Aggregate failures; do NOT spam per-element
    elem_fail = 0
    viewspecific_fail = 0
    collector_fail = None

    scanned = 0
    budget_triggered = False
    budget_reason = None
    t0 = time_fn()

    try:
        collector = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()

        for elem in collector:
            scanned += 1

            # Budget checks are evaluated before doing per-element work
            if max_elements is not None and scanned > int(max_elements):
                budget_triggered = True
                budget_reason = "max_elements"
                break

            if time_budget_s is not None and (time_fn() - t0) > float(time_budget_s):
                budget_triggered = True
                budget_reason = "time_budget_s"
                break

            try:
                bbox = elem.get_BoundingBox(view)
                if bbox is None:
                    continue

                # In model views, skip view-specific annotations for the *model* extents
                if not is_drafting:
                    try:
                        if bool(getattr(elem, "ViewSpecific", False)):
                            continue
                    except Exception:
                        viewspecific_fail += 1
                        # Preserve prior behavior: don't skip on failure; just treat as not view-specific
                        pass

                mn = bbox.Min
                mx = bbox.Max

                corners_local = [
                    XYZ(mn.X, mn.Y, mn.Z),
                    XYZ(mx.X, mn.Y, mn.Z),
                    XYZ(mn.X, mx.Y, mn.Z),
                    XYZ(mx.X, mx.Y, mn.Z),
                    XYZ(mn.X, mn.Y, mx.Z),
                    XYZ(mx.X, mn.Y, mx.Z),
                    XYZ(mn.X, mx.Y, mx.Z),
                    XYZ(mx.X, mx.Y, mx.Z),
                ]

                TB = getattr(bbox, "Transform", None)
                if TB is not None:
                    corners_world = [TB.OfPoint(p) for p in corners_local]
                else:
                    corners_world = corners_local

                for p in corners_world:
                    u, v = basis.transform_to_view_uv((p.X, p.Y, p.Z))
                    min_u = min(min_u, u)
                    min_v = min(min_v, v)
                    max_u = max(max_u, u)
                    max_v = max(max_v, v)

                found += 1
            except Exception:
                elem_fail += 1
                continue

    except Exception as e:
        collector_fail = e

    elapsed_s = max(0.0, float(time_fn() - t0))

    # Diagnostics for collector failure
    if collector_fail is not None and diag is not None:
        try:
            diag.warn(
                phase="bounds",
                callsite="synthetic_bounds_from_visible_extents",
                message="Failed to scan visible extents; using default bounds",
                view_id=view_id,
                extra={"exc_type": type(collector_fail).__name__, "exc": str(collector_fail)},
            )
        except Exception:
            pass

    # Budget-trigger diagnostic (explicit, aggregated, non-spammy)
    if budget_triggered and diag is not None:
        try:
            diag.warn(
                phase="bounds",
                callsite="synthetic_bounds_from_visible_extents.budget",
                message="Visible-extents scan stopped early due to budget; bounds confidence reduced",
                view_id=view_id,
                extra={
                    "budget_reason": budget_reason,
                    "max_elements": int(max_elements) if max_elements is not None else None,
                    "time_budget_s": float(time_budget_s) if time_budget_s is not None else None,
                    "scanned": int(scanned),
                    "found": int(found),
                    "elapsed_s": float(elapsed_s),
                    "elem_fail": int(elem_fail),
                    "viewspecific_fail": int(viewspecific_fail),
                    "is_drafting": bool(is_drafting),
                },
            )
        except Exception:
            pass

    # If scan yielded no usable bounds, fallback to default
    if found == 0 or min_u == float("inf") or collector_fail is not None:
        # Preserve existing explicit warning for empty scans
        if collector_fail is None and diag is not None:
            try:
                diag.warn(
                    phase="bounds",
                    callsite="synthetic_bounds_from_visible_extents",
                    message="No elements found for synthetic bounds; using default 200x200ft bounds",
                    view_id=view_id,
                    extra={
                        "found": int(found),
                        "scanned": int(scanned),
                        "elem_fail": int(elem_fail),
                        "viewspecific_fail": int(viewspecific_fail),
                        "is_drafting": bool(is_drafting),
                        "budget_triggered": bool(budget_triggered),
                        "budget_reason": budget_reason,
                    },
                )
            except Exception:
                pass

        return {
            "bounds_uv": default_bounds,
            "confidence": "low",
            "budget": {
                "triggered": bool(budget_triggered),
                "reason": budget_reason,
                "scanned": int(scanned),
                "found": int(found),
                "elapsed_s": float(elapsed_s),
                "max_elements": int(max_elements) if max_elements is not None else None,
                "time_budget_s": float(time_budget_s) if time_budget_s is not None else None,
            },
        }

    # If there were per-element failures, record aggregated counts once
    if (elem_fail > 0 or viewspecific_fail > 0) and diag is not None:
        try:
            diag.warn(
                phase="bounds",
                callsite="synthetic_bounds_from_visible_extents",
                message="Element failures occurred during visible extents scan (aggregated)",
                view_id=view_id,
                extra={
                    "found": int(found),
                    "scanned": int(scanned),
                    "elem_fail": int(elem_fail),
                    "viewspecific_fail": int(viewspecific_fail),
                    "is_drafting": bool(is_drafting),
                    "budget_triggered": bool(budget_triggered),
                    "budget_reason": budget_reason,
                },
            )
        except Exception:
            pass

    bounds = Bounds2D(min_u - buffer, min_v - buffer, max_u + buffer, max_v + buffer)
    confidence = "low" if budget_triggered else "med"

    return {
        "bounds_uv": bounds,
        "confidence": confidence,
        "budget": {
            "triggered": bool(budget_triggered),
            "reason": budget_reason,
            "scanned": int(scanned),
            "found": int(found),
            "elapsed_s": float(elapsed_s),
            "max_elements": int(max_elements) if max_elements is not None else None,
            "time_budget_s": float(time_budget_s) if time_budget_s is not None else None,
        },
    }

def _bounds_to_tuple(b):
    try:
        return (float(b.xmin), float(b.ymin), float(b.xmax), float(b.ymax))
    except Exception:
        return None


def resolve_view_bounds(view, diag=None, policy=None):
    """Resolve view bounds in view-local UV and return auditable metadata.

    Contract:
        - Always returns a dict with: bounds_uv, reason, confidence, capped, cap_before, cap_after
        - Never performs a "silent cap": if cap triggers, caller gets before/after
        - Centralizes: crop-box bounds, synthetic visible-extents bounds, annotation-driven expansion

    Args:
        view: Revit View (or stub)
        diag: Diagnostics (optional)
        policy: dict (optional) with the following keys:

            doc: Revit Document (required unless providing all *_fn callbacks)
            basis: ViewBasis (required unless providing all *_fn callbacks)
            cfg: Config (optional, used for annotation settings)

            buffer_ft: float (default 0.0)
            cell_size_ft: float (required for cap reporting)
            max_W: int (optional)
            max_H: int (optional)

            crop_active: bool (optional; overrides view.CropBoxActive)
            bounds_crop_fn(view, policy) -> Bounds2D
            bounds_extents_fn(view, policy) -> Bounds2D
            bounds_default_fn(view, policy) -> Bounds2D
            anno_expand_fn(base_bounds, view, policy) -> Bounds2D|None

    Returns:
        dict with:
            bounds_uv: Bounds2D
            reason: "crop"|"extents"|"fallback"
            confidence: "high"|"med"|"low"
            capped: bool
            cap_before: dict|None
            cap_after: dict|None
            anno_expanded: bool
            grid_W: int
            grid_H: int

    Notes:
        - Unit-testable without Autodesk imports by supplying *_fn callbacks in policy.
    """
    from ..core.math_utils import Bounds2D
    import math

    policy = policy or {}

    view_id = None
    try:
        view_id = getattr(getattr(view, "Id", None), "IntegerValue", None)
    except Exception:
        view_id = None

    buffer_ft = float(policy.get("buffer_ft", 0.0) or 0.0)

    cell_size_ft = policy.get("cell_size_ft", None)
    if cell_size_ft is None:
        raise ValueError("resolve_view_bounds requires policy['cell_size_ft']")
    cell_size_ft = float(cell_size_ft)
    if cell_size_ft <= 0:
        raise ValueError("resolve_view_bounds requires cell_size_ft > 0")

    max_W = policy.get("max_W", None)
    max_H = policy.get("max_H", None)

    # 1) Base bounds (crop preferred when active)
    reason = "fallback"
    confidence = "low"
    base_bounds = None
    model_bounds = None
    bounds_budget = None

    crop_active = policy.get("crop_active", None)
    if crop_active is None:
        try:
            crop_active = bool(getattr(view, "CropBoxActive"))
        except Exception:
            crop_active = False

    bounds_crop_fn = policy.get("bounds_crop_fn", None)
    bounds_extents_fn = policy.get("bounds_extents_fn", None)
    bounds_default_fn = policy.get("bounds_default_fn", None)

    if bounds_default_fn is None:

        def bounds_default_fn(_view, _policy):
            return Bounds2D(
                -100.0 - buffer_ft,
                -100.0 - buffer_ft,
                100.0 + buffer_ft,
                100.0 + buffer_ft,
            )

    if crop_active:
        try:
            if bounds_crop_fn is not None:
                base_bounds = bounds_crop_fn(view, policy)
            else:
                doc = policy.get("doc")
                basis = policy.get("basis")
                if doc is None or basis is None:
                    raise ValueError(
                        "policy must provide doc and basis when bounds_crop_fn is not supplied"
                    )
                # Base bounds drives raster sizing (may include buffer_ft).
                base_bounds = xy_bounds_from_crop_box_all_corners(view, basis, buffer=buffer_ft)

                # Model clip bounds must be the true crop (NO buffer), so model ink does not extend past crop.
                model_bounds = xy_bounds_from_crop_box_all_corners(view, basis, buffer=0.0)

            reason = "crop"
            confidence = "high"          

        except Exception as e:
            if diag is not None:
                try:
                    diag.warn(
                        phase="bounds",
                        callsite="resolve_view_bounds.crop",
                        message="Crop bounds failed; falling back to extents",
                        view_id=view_id,
                        extra={"exc_type": type(e).__name__, "exc": str(e)},
                    )
                except Exception:
                    pass

    if base_bounds is None:
        try:
            if bounds_extents_fn is not None:
                r_ext = bounds_extents_fn(view, policy)
            else:
                doc = policy.get("doc")
                basis = policy.get("basis")
                if doc is None or basis is None:
                    raise ValueError(
                        "policy must provide doc and basis when bounds_extents_fn is not supplied"
                    )

                cfg = policy.get("cfg", None)

                # PR11: budgets can be overridden by policy, otherwise come from Config, otherwise None.
                if "extents_scan_max_elements" in policy:
                    max_elements = policy["extents_scan_max_elements"]
                else:
                    max_elements = getattr(cfg, "extents_scan_max_elements", None)

                if "extents_scan_time_budget_s" in policy:
                    time_budget_s = policy["extents_scan_time_budget_s"]
                else:
                    time_budget_s = getattr(cfg, "extents_scan_time_budget_s", None)


                r_ext = synthetic_bounds_from_visible_extents(
                    doc,
                    view,
                    basis,
                    buffer=buffer_ft,
                    diag=diag,
                    max_elements=max_elements,
                    time_budget_s=time_budget_s,
                )

            if isinstance(r_ext, dict):
                base_bounds = r_ext.get("bounds_uv")
                bounds_budget = r_ext.get("budget")
                confidence = r_ext.get("confidence") or "med"
            else:
                base_bounds = r_ext
                bounds_budget = None
                confidence = "med"

            reason = "extents"
        except Exception as e:
            base_bounds = bounds_default_fn(view, policy)
            reason = "fallback"
            confidence = "low"
            bounds_budget = None
            if diag is not None:
                try:
                    diag.warn(
                        phase="bounds",
                        callsite="resolve_view_bounds.extents",
                        message="Extents bounds failed; using default fallback bounds",
                        view_id=view_id,
                        extra={"exc_type": type(e).__name__, "exc": str(e)},
                    )
                except Exception:
                    pass

    # 2) Annotation-driven expansion (optional)
    anno_expanded = False
    try:
        anno_expand_fn = policy.get("anno_expand_fn", None)
        if anno_expand_fn is None:
            doc = policy.get("doc")
            basis = policy.get("basis")
            cfg = policy.get("cfg")
            if doc is not None and basis is not None and cfg is not None:
                from .annotation import compute_annotation_extents

                anno_bounds = compute_annotation_extents(
                    doc,
                    view,
                    basis,
                    base_bounds,
                    cell_size_ft,
                    cfg,
                    diag=diag,
                )
            else:
                anno_bounds = None
        else:
            anno_bounds = anno_expand_fn(base_bounds, view, policy)

        if anno_bounds is not None:
            base_bounds = anno_bounds
            anno_expanded = True
    except Exception as e:
        # Annotation expansion failing should never block model export.
        if diag is not None:
            try:
                diag.warn(
                    phase="bounds",
                    callsite="resolve_view_bounds.annotation",
                    message="Annotation bounds expansion failed; continuing with base bounds",
                    view_id=view_id,
                    extra={"exc_type": type(e).__name__, "exc": str(e)},
                )
            except Exception:
                pass
                
    # 3) Cap reporting (optional)
    width_ft = float(base_bounds.width())
    height_ft = float(base_bounds.height())

    cell_size_ft_requested = float(cell_size_ft)

    W_req = max(1, int(math.ceil(width_ft / cell_size_ft_requested)))
    H_req = max(1, int(math.ceil(height_ft / cell_size_ft_requested)))

    cap_triggered = False
    cap_before = None
    cap_after = None

    resolution_mode = "canonical"
    cell_size_ft_effective = float(cell_size_ft_requested)

    if max_W is not None and max_H is not None:
        max_W = int(max_W)
        max_H = int(max_H)

        if W_req > max_W or H_req > max_H:
            cap_triggered = True

            cap_before = {
                "W": int(W_req),
                "H": int(H_req),
                "bounds_uv": _bounds_to_tuple(base_bounds),
                "cell_size_ft": float(cell_size_ft_requested),
            }

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # FIX: ADAPTIVE CELL SIZE (not bounds clipping)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

            # Calculate minimum cell size that fits bounds within cap
            cell_size_w = width_ft / float(max_W)
            cell_size_h = height_ft / float(max_H)
            cell_size_ft_effective = max(
                cell_size_w,
                cell_size_h,
                cell_size_ft_requested  # Never decrease below requested
            )

            # Recalculate grid dimensions with adaptive cell size
            W_adapt = max(1, int(math.ceil(width_ft / cell_size_ft_effective)))
            H_adapt = max(1, int(math.ceil(height_ft / cell_size_ft_effective)))

            # Apply final cap (should now be within limits, but defensive)
            W_adapt = min(W_adapt, max_W)
            H_adapt = min(H_adapt, max_H)

            resolution_mode = "adaptive"

            # CRITICAL: Bounds stay UNCHANGED
            # The raster covers the full visible area at lower resolution
            # No geometry is lost outside the raster bounds

            # Report cap-after as the policy cap applied to the REQUESTED grid.
            # This is an auditable before/after reporting surface (unit-test contract).
            W_capped = min(int(W_req), int(max_W))
            H_capped = min(int(H_req), int(max_H))

            cap_after = {
                "W": int(W_capped),
                "H": int(H_capped),
                "bounds_uv": _bounds_to_tuple(base_bounds),  # UNCHANGED
                "cell_size_ft": float(cell_size_ft_effective),
            }


            if diag is not None:
                try:
                    diag.warn(
                        phase="bounds",
                        callsite="resolve_view_bounds.adaptive_resolution",
                        message="Grid size exceeds maximum; using adaptive cell size (bounds preserved)",
                        view_id=view_id,
                        extra={
                            "before": cap_before,
                            "after": cap_after,
                            "max_W": max_W,
                            "max_H": max_H,
                            "resolution_mode": resolution_mode,
                            "cell_size_increase_factor": float(cell_size_ft_effective / cell_size_ft_requested),
                        },
                    )
                except Exception:
                    pass

    # Report final grid dimensions using effective cell size
    W = max(1, int(math.ceil(width_ft / cell_size_ft_effective)))
    H = max(1, int(math.ceil(height_ft / cell_size_ft_effective)))

    if max_W is not None:
        W = min(int(W), int(max_W))
    if max_H is not None:
        H = min(int(H), int(max_H))

    return {
        "bounds_uv": base_bounds,
       
        # Pre-annotation bounds (crop/extents/fallback). Used by pipeline to clip model ink
        # even when raster bounds are expanded for annotations.
        "model_bounds_uv": model_bounds,

        "reason": reason,
        "confidence": confidence,
        "anno_expanded": bool(anno_expanded),

        # Back-compat: 'capped' previously meant bounds were clipped; now it means cap policy triggered.
        "capped": bool(cap_triggered),
        "cap_before": cap_before,
        "cap_after": cap_after,

        "grid_W": int(W),
        "grid_H": int(H),
        "buffer_ft": buffer_ft,

        # Requested vs effective resolution (Option 2 contract)
        "resolution_mode": resolution_mode,
        "cap_triggered": bool(cap_triggered),
        "cell_size_ft_requested": float(cell_size_ft_requested),
        "cell_size_ft_effective": float(cell_size_ft_effective),

        # Back-compat: preserve existing key name, but keep it as requested (not effective).
        "cell_size_ft": float(cell_size_ft_effective),

        "bounds_budget": bounds_budget,
    }


def _view_type_name(view):
    """
    Best-effort, Revit-free view type name extraction for gating + tests.
    Returns a stable string (e.g., 'FloorPlan', 'DraftingView') or ''.

    NOTE: In Dynamo/Revit, View.ViewType may stringify as a number (e.g. '1').
    We include a conservative numeric mapping to avoid rejecting valid plan/section views.
    """
    if view is None:
        return ""

    try:
        vt = getattr(view, "ViewType", None)
        if vt is None:
            return ""

        # 0) If Autodesk is available, prefer direct enum comparisons (avoids brittle numeric maps).
        try:
            from Autodesk.Revit.DB import ViewType as _VT

            # IMPORTANT: In some Dynamo contexts, ViewType may stringify as an int (e.g. "11"),
            # so comparisons against the enum constants are the most reliable.
            if hasattr(_VT, "Legend") and (vt == getattr(_VT, "Legend", None)):
                return "Legend"
            if hasattr(_VT, "DraftingView") and (vt == getattr(_VT, "DraftingView", None)):
                return "DraftingView"
            if hasattr(_VT, "Section") and (vt == getattr(_VT, "Section", None)):
                return "Section"
            if hasattr(_VT, "FloorPlan") and (vt == getattr(_VT, "FloorPlan", None)):
                return "FloorPlan"
            if hasattr(_VT, "CeilingPlan") and (vt == getattr(_VT, "CeilingPlan", None)):
                return "CeilingPlan"
            if hasattr(_VT, "Elevation") and (vt == getattr(_VT, "Elevation", None)):
                return "Elevation"
            if hasattr(_VT, "Detail") and (vt == getattr(_VT, "Detail", None)):
                return "Detail"
            if hasattr(_VT, "AreaPlan") and (vt == getattr(_VT, "AreaPlan", None)):
                return "AreaPlan"
            if hasattr(_VT, "EngineeringPlan") and (vt == getattr(_VT, "EngineeringPlan", None)):
                return "EngineeringPlan"
            if hasattr(_VT, "ThreeD") and (vt == getattr(_VT, "ThreeD", None)):
                return "ThreeD"
        except Exception:
            pass

        # 1) If the enum stringifies to a meaningful name, use it.
        try:
            s = str(vt) or ""
            s_clean = s.split(".")[-1]
            # If it's not purely numeric, assume it's already a name like "FloorPlan"
            if s_clean and not s_clean.isdigit():
                return s_clean
        except Exception:
            pass

        # 2) Some stubs expose Name on the enum
        try:
            name = getattr(vt, "Name", "") or ""
            if name:
                return name
        except Exception:
            pass

        # 3) Fallback: numeric mapping (covers int-valued enums or numeric stringification)
        # This mapping is conservative and can be extended as you encounter more values.
        try:
            if isinstance(vt, int):
                code = vt
            else:
                code = int(str(vt))
        except Exception:
            return ""

        # Common Revit ViewType codes (observed in some Dynamo contexts)
        # If a code is unknown, return numeric string to keep it visible in diagnostics.
        code_map = {
            1: "FloorPlan",
            2: "CeilingPlan",
            3: "Elevation",
            4: "ThreeD",
            5: "Schedule",
            6: "DrawingSheet",
            7: "ProjectBrowser",
            8: "Report",
            9: "DraftingView",
            10: "Legend",
            11: "Section",
            12: "Detail",
            13: "Rendering",
            14: "Walkthrough",
            15: "SystemBrowser",
            16: "CostReport",
            17: "LoadReport",
            18: "ColumnSchedule",
            19: "PanelSchedule",
            20: "PresureLossReport",
            21: "AreaPlan",
            22: "EngineeringPlan",
        }
        return code_map.get(code, str(code))

    except Exception:
        return ""

def supports_model_geometry(view, diag=None):
    """
    Capability: view can reasonably be expected to host model geometry in this pipeline.

    IMPORTANT: Drafting views are explicitly treated as 'no model truth' even if some APIs exist.
    """
    if view is None:
        return False

    try:
        if bool(getattr(view, "IsTemplate", False)):
            return False
    except Exception:
        # If we cannot read IsTemplate, do not assume it's safe
        return False

    vt = _view_type_name(view)
    if vt in ("DraftingView",):
        return False

    # Conservative allow-list: if it looks like a 2D model view, treat as model-capable.
    # (We avoid Autodesk imports so unit tests can run.)
    if vt in (
        "FloorPlan",
        "CeilingPlan",
        "EngineeringPlan",
        "AreaPlan",
        "Section",
        "Elevation",
        "Detail",  # callouts/detail views can still show model geometry
    ):
        return True

    # Unknown types: do NOT assume model truth.
    return False


def supports_crop_bounds(view, diag=None):
    """
    Capability: view supports crop-based bounds (not whether crop is active).
    Drafting views are treated as NO for crop-model-bounds purposes.
    """
    if view is None:
        return False

    if not supports_model_geometry(view, diag=diag):
        return False

    # If CropBox can be accessed without throwing, we treat crop-bounds as supported.
    try:
        _ = getattr(view, "CropBox", None)
        return True
    except Exception:
        return False


def supports_depth(view, diag=None):
    """
    Capability: pipeline depth semantics are meaningful.
    In this pipeline, depth is only meaningful if model geometry is meaningful.
    """
    return bool(supports_model_geometry(view, diag=diag))


# Explicit “modes”
VIEW_MODE_MODEL_AND_ANNOTATION = "MODEL_AND_ANNOTATION"
VIEW_MODE_ANNOTATION_ONLY = "ANNOTATION_ONLY"
VIEW_MODE_REJECTED = "REJECTED"


def resolve_view_mode(view, diag=None, policy=None):
    """
    Decide how this view should be processed, and WHY.
    Returns: (mode, reason_dict)
    """
    reason = {
        "view_type": _view_type_name(view),
        "is_template": None,
        "supports_model_geometry": None,
        "supports_crop_bounds": None,
        "supports_depth": None,
    }

    if view is None:
        return VIEW_MODE_REJECTED, {**reason, "why": "view_is_none"}

    try:
        reason["is_template"] = bool(getattr(view, "IsTemplate", False))
        if reason["is_template"]:
            return VIEW_MODE_REJECTED, {**reason, "why": "view_is_template"}
    except Exception:
        # Hard-fail conservative: we cannot safely classify this view
        return VIEW_MODE_REJECTED, {**reason, "why": "cannot_read_is_template"}

    reason["supports_model_geometry"] = supports_model_geometry(view, diag=diag)
    reason["supports_crop_bounds"] = supports_crop_bounds(view, diag=diag)
    reason["supports_depth"] = supports_depth(view, diag=diag)

    vt = reason["view_type"]

    # Drafting view is explicitly annotation-only (PR6 contract)
    if vt == "DraftingView":
        return VIEW_MODE_ANNOTATION_ONLY, {**reason, "why": "drafting_view_forced_annotation_only"}

    # Legend views should be processed as annotation-only (no model truth, but visible symbols/notes)
    if vt == "Legend":
        return VIEW_MODE_ANNOTATION_ONLY, {**reason, "why": "legend_forced_annotation_only"}

    # If it is not model-capable, reject rather than “pretend” (prevents silent semantic drift)
    if not reason["supports_model_geometry"]:
        return VIEW_MODE_REJECTED, {**reason, "why": "no_model_geometry_capability"}

    # Model-capable views run full pipeline
    return VIEW_MODE_MODEL_AND_ANNOTATION, {**reason, "why": "model_capable_view"}


def resolve_annotation_only_bounds(doc, view, basis, cell_size_ft, cfg=None, diag=None):
    """
    Produce bounds from annotation extents ONLY (no union with model/crop).
    This is required for drafting views; otherwise fallback base bounds dominate.
    """
    from ..core.math_utils import Bounds2D
    from .annotation import collect_2d_annotations, is_extent_driver_annotation

    view_id = None
    try:
        view_id = getattr(getattr(view, "Id", None), "IntegerValue", None)
    except Exception:
        view_id = None

    # Collect view-specific annotations and compute UV extents
    try:
        annos = collect_2d_annotations(doc, view)
    except Exception as e:
        if diag is not None:
            diag.warn(
                phase="bounds",
                callsite="resolve_annotation_only_bounds.collect",
                message="Failed to collect annotations for annotation-only bounds",
                view_id=view_id,
                extra={"exc_type": type(e).__name__, "exc": str(e)},
            )
        return None

    drivers = []
    for elem, _atype in annos:
        try:
            if is_extent_driver_annotation(elem):
                drivers.append(elem)
        except Exception:
            continue

    if not drivers:
        # For Legend/Drafting, be robust: if no extent-driver annotations were detected,
        # fall back to using ALL collected 2D annotations for bounds.
        drivers = [elem for (elem, _atype) in annos if elem is not None]
        if not drivers:
            return None
        if diag is not None:
            try:
                diag.warn(
                    phase="bounds",
                    callsite="resolve_annotation_only_bounds",
                    message="No extent-driver annotations found; falling back to all 2D annotations for bounds",
                    view_id=view_id,
                    extra={"num_annos": len(annos)},
                )
            except Exception:
                pass

    min_u = min_v = max_u = max_v = None

    for elem in drivers:
        try:
            bbox = elem.get_BoundingBox(view)
            if bbox is None:
                continue
            mn = bbox.Min
            mx = bbox.Max

            # Project bbox corners to UV (best-effort)
            pts = [
                (mn.X, mn.Y, mn.Z),
                (mx.X, mn.Y, mn.Z),
                (mn.X, mx.Y, mn.Z),
                (mx.X, mx.Y, mn.Z),
                (mn.X, mn.Y, mx.Z),
                (mx.X, mn.Y, mx.Z),
                (mn.X, mx.Y, mx.Z),
                (mx.X, mx.Y, mx.Z),
            ]
            for p in pts:
                u, v = basis.transform_to_view_uv(p)
                if min_u is None:
                    min_u = max_u = u
                    min_v = max_v = v
                else:
                    min_u = min(min_u, u)
                    min_v = min(min_v, v)
                    max_u = max(max_u, u)
                    max_v = max(max_v, v)
        except Exception as e:
            if diag is not None:
                diag.warn(
                    phase="bounds",
                    callsite="resolve_annotation_only_bounds.bbox",
                    message="Failed to process annotation bbox for annotation-only bounds; skipping element",
                    view_id=view_id,
                    elem_id=getattr(getattr(elem, "Id", None), "IntegerValue", None),
                    extra={"exc_type": type(e).__name__, "exc": str(e)},
                )
            continue

    if min_u is None:
        return None

    # Apply a small padding (1 cell) so edge-touching annotations do not clip
    pad = float(cell_size_ft)
    return Bounds2D(min_u - pad, min_v - pad, max_u + pad, max_v + pad)
