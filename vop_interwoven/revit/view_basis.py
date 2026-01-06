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
