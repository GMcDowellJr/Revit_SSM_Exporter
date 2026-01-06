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

        # Forward = ViewDirection (keep as XYZ)
        try:
            forward = view.ViewDirection.Normalize()
        except Exception:
            forward = right.CrossProduct(up).Normalize()

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

def synthetic_bounds_from_visible_extents(doc, view, basis, buffer=0.0, diag=None):
    """Compute synthetic bounds from element extents in a view (crop-off / no-crop views)."""
    from ..core.math_utils import Bounds2D
    from Autodesk.Revit.DB import FilteredElementCollector, ViewDrafting, XYZ

    view_id = None
    try:
        view_id = getattr(getattr(view, "Id", None), "IntegerValue", None)
    except Exception:
        view_id = None

    is_drafting = isinstance(view, ViewDrafting)

    min_u = float("inf")
    min_v = float("inf")
    max_u = float("-inf")
    max_v = float("-inf")
    found = 0

    # Aggregate failures; do NOT spam per-element
    elem_fail = 0
    viewspecific_fail = 0
    collector_fail = None

    try:
        collector = (
            FilteredElementCollector(doc, view.Id)
            .WhereElementIsNotElementType()
        )

        for elem in collector:
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

    if collector_fail is not None:
        try:
            if diag is not None:
                diag.warn(
                    phase="bounds",
                    callsite="synthetic_bounds_from_visible_extents",
                    message="Failed to scan visible extents; falling back to default bounds",
                    view_id=view_id,
                    extra={"exc_type": type(collector_fail).__name__, "exc": str(collector_fail)},
                )
        except Exception:
            pass

    # If scan yielded no usable bounds, fallback to default
    if found == 0 or min_u == float("inf"):
        try:
            if diag is not None:
                diag.warn(
                    phase="bounds",
                    callsite="synthetic_bounds_from_visible_extents",
                    message="No elements found for synthetic bounds; using default 200x200ft bounds",
                    view_id=view_id,
                    extra={
                        "found": found,
                        "elem_fail": elem_fail,
                        "viewspecific_fail": viewspecific_fail,
                        "is_drafting": bool(is_drafting),
                    },
                )
        except Exception:
            pass

        return Bounds2D(-100.0 - buffer, -100.0 - buffer, 100.0 + buffer, 100.0 + buffer)

    # If there were per-element failures, record aggregated counts once
    if (elem_fail > 0 or viewspecific_fail > 0) and diag is not None:
        try:
            diag.warn(
                phase="bounds",
                callsite="synthetic_bounds_from_visible_extents",
                message="Element failures occurred during visible extents scan (aggregated)",
                view_id=view_id,
                extra={
                    "found": found,
                    "elem_fail": elem_fail,
                    "viewspecific_fail": viewspecific_fail,
                    "is_drafting": bool(is_drafting),
                },
            )
        except Exception:
            pass

    return Bounds2D(min_u - buffer, min_v - buffer, max_u + buffer, max_v + buffer)

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
                base_bounds = xy_bounds_from_crop_box_all_corners(view, basis, buffer=buffer_ft)

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
                base_bounds = bounds_extents_fn(view, policy)
            else:
                doc = policy.get("doc")
                basis = policy.get("basis")
                if doc is None or basis is None:
                    raise ValueError(
                        "policy must provide doc and basis when bounds_extents_fn is not supplied"
                    )
                base_bounds = synthetic_bounds_from_visible_extents(
                    doc, view, basis, buffer=buffer_ft, diag=diag
                )

            reason = "extents"
            confidence = "med"
        except Exception as e:
            base_bounds = bounds_default_fn(view, policy)
            reason = "fallback"
            confidence = "low"
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

    W = max(1, int(math.ceil(width_ft / cell_size_ft)))
    H = max(1, int(math.ceil(height_ft / cell_size_ft)))

    capped = False
    cap_before = None
    cap_after = None

    if max_W is not None and max_H is not None:
        max_W = int(max_W)
        max_H = int(max_H)
        if W > max_W or H > max_H:
            capped = True
            cap_before = {"W": W, "H": H, "bounds_uv": _bounds_to_tuple(base_bounds)}
            W2 = min(W, max_W)
            H2 = min(H, max_H)

            # Preserve xmin/ymin anchor, clamp xmax/ymax
            base_bounds = base_bounds.__class__(
                base_bounds.xmin,
                base_bounds.ymin,
                base_bounds.xmin + W2 * cell_size_ft,
                base_bounds.ymin + H2 * cell_size_ft,
            )
            cap_after = {"W": W2, "H": H2, "bounds_uv": _bounds_to_tuple(base_bounds)}

            if diag is not None:
                try:
                    diag.warn(
                        phase="bounds",
                        callsite="resolve_view_bounds.cap",
                        message="Grid size exceeds maximum; bounds were capped",
                        view_id=view_id,
                        extra={
                            "before": cap_before,
                            "after": cap_after,
                            "max_W": max_W,
                            "max_H": max_H,
                        },
                    )
                except Exception:
                    pass

            W, H = W2, H2

    return {
        "bounds_uv": base_bounds,
        "reason": reason,
        "confidence": confidence,
        "anno_expanded": bool(anno_expanded),
        "capped": bool(capped),
        "cap_before": cap_before,
        "cap_after": cap_after,
        "grid_W": int(W),
        "grid_H": int(H),
        "buffer_ft": buffer_ft,
        "cell_size_ft": cell_size_ft,
    }
