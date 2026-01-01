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

    def __repr__(self):
        return f"ViewBasis(origin={self.origin}, right={self.right}, up={self.up}, forward={self.forward})"


def make_view_basis(view):
    """Extract view basis from Revit View.

    Args:
        view: Revit View object

    Returns:
        ViewBasis with origin and basis vectors

    Commentary:
        ⚠ This is a placeholder implementation. Full implementation requires:
           - Access to view.Origin, view.RightDirection, view.UpDirection, view.ViewDirection
           - Handling of different view types (plans, sections, elevations, 3D)
           - Fallback for views without explicit basis (e.g., drafting views)
        ✔ For now, returns identity basis for testing

    Example (with actual Revit API):
        >>> # view = some Revit FloorPlan view
        >>> # basis = make_view_basis(view)
        >>> # basis.is_plan_like()
        >>> # True
    """
    # TODO: Implement actual Revit API access
    # For now, return identity basis as placeholder
    return ViewBasis(
        origin=(0.0, 0.0, 0.0),
        right=(1.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        forward=(0.0, 0.0, -1.0),
    )


def xy_bounds_from_crop_box_all_corners(view, basis, buffer=0.0):
    """Compute XY bounds from view crop box (all 8 corners method).

    Args:
        view: Revit View object
        basis: ViewBasis for coordinate transformation
        buffer: Additional margin to add (in model units)

    Returns:
        Bounds2D in view-local XY coordinates

    Commentary:
        ⚠ This is a placeholder. Full implementation requires:
           - Access to view.CropBox (BoundingBoxXYZ)
           - Transformation of all 8 corners using view transform
           - Computation of axis-aligned bounds in view UV space
        ✔ Prefer this method over CropBox.Min/Max for rotated views
    """
    # TODO: Implement actual crop box extraction
    from ..core.math_utils import Bounds2D

    # Placeholder: return 100x100 bounds
    return Bounds2D(-50.0 - buffer, -50.0 - buffer, 50.0 + buffer, 50.0 + buffer)


def synthetic_bounds_from_visible_extents(doc, view, basis, buffer=0.0):
    """Compute synthetic bounds from visible element extents (for crop-off views).

    Args:
        doc: Revit Document
        view: Revit View
        basis: ViewBasis
        buffer: Additional margin

    Returns:
        Bounds2D in view-local XY coordinates

    Commentary:
        ⚠ This is a placeholder. Full implementation requires:
           - Element collection in view
           - Bounding box aggregation
           - Outlier filtering (clustering by percentile)
        ✔ Only needed when CropBoxActive is False
    """
    # TODO: Implement visible extent calculation
    from ..core.math_utils import Bounds2D

    return Bounds2D(-100.0 - buffer, -100.0 - buffer, 100.0 + buffer, 100.0 + buffer)
