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


def make_view_basis(view):
    """Extract view basis from Revit View.

    Args:
        view: Revit View object

    Returns:
        ViewBasis with origin and basis vectors

    Commentary:
        ✔ Extracts view coordinate system from Revit View
        ✔ Handles plan views, sections, elevations, and 3D views
        ✔ Forward vector computed as Right × Up (into screen)
        ⚠ For drafting views or views without orientation, falls back to identity

    Example:
        >>> # view = some Revit FloorPlan view
        >>> basis = make_view_basis(view)
        >>> basis.is_plan_like()
        >>> # True for plan views (forward points down)
    """
    from Autodesk.Revit.DB import View

    try:
        # Get view origin and direction vectors
        origin = view.Origin
        right = view.RightDirection
        up = view.UpDirection

        # Compute forward vector (into screen) as right × up
        # Note: Revit's ViewDirection points opposite to our forward
        forward = right.CrossProduct(up).Normalize()

        return ViewBasis(
            origin=(origin.X, origin.Y, origin.Z),
            right=(right.X, right.Y, right.Z),
            up=(up.X, up.Y, up.Z),
            forward=(forward.X, forward.Y, forward.Z),
        )

    except AttributeError:
        # Fallback for views without explicit basis (e.g., drafting views)
        # Return identity basis (plan-like view looking down Z)
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
            return Bounds2D(-100.0 - buffer, -100.0 - buffer, 100.0 + buffer, 100.0 + buffer)

        # BoundingBoxXYZ: Min/Max are in the box local space; Transform maps them to model space.
        T = getattr(crop_box, "Transform", None)

        min_pt = crop_box.Min
        max_pt = crop_box.Max

        # Build 8 corners in crop-box-local coordinates.
        try:
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
        except Exception:
            # Very defensive fallback: treat Min/Max as tuples if XYZ isn't available.
            corners_local = [
                (min_pt.X, min_pt.Y, min_pt.Z),
                (max_pt.X, min_pt.Y, min_pt.Z),
                (min_pt.X, max_pt.Y, min_pt.Z),
                (max_pt.X, max_pt.Y, min_pt.Z),
                (min_pt.X, min_pt.Y, max_pt.Z),
                (max_pt.X, min_pt.Y, max_pt.Z),
                (min_pt.X, max_pt.Y, max_pt.Z),
                (max_pt.X, max_pt.Y, max_pt.Z),
            ]

        # Transform to model/world coordinates if a transform is present.
        corners_world = []
        if T is not None:
            for p in corners_local:
                try:
                    pw = T.OfPoint(p)  # XYZ -> XYZ
                    corners_world.append((pw.X, pw.Y, pw.Z))
                except Exception:
                    # If p is tuple or OfPoint fails, fall back to original coords.
                    if isinstance(p, tuple):
                        corners_world.append(p)
                    else:
                        corners_world.append((p.X, p.Y, p.Z))
        else:
            for p in corners_local:
                if isinstance(p, tuple):
                    corners_world.append(p)
                else:
                    corners_world.append((p.X, p.Y, p.Z))

        # Project to view UV and compute axis-aligned bounds.
        corners_uv = [basis.transform_to_view_uv(p) for p in corners_world]

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
def synthetic_bounds_from_visible_extents(doc, view, basis, buffer=0.0):
    """Compute synthetic bounds from visible element extents (for crop-off views).

    Approximates clicking "Crop View" in Revit by calculating bounds from:
    - 3D model geometry (for plan/section/elevation views)
    - 2D annotation elements (for drafting views)

    Args:
        doc: Revit Document
        view: Revit View
        basis: ViewBasis
        buffer: Additional margin in feet

    Returns:
        Bounds2D in view-local XY coordinates

    Commentary:
        ✔ Collects visible elements and computes aggregate bounds
        ✔ Handles drafting views (use 2D elements only)
        ✔ Handles plan/section/elevation (use 3D model geometry)
        ✔ Falls back to default bounds if no elements found
    """
    from ..core.math_utils import Bounds2D
    from Autodesk.Revit.DB import FilteredElementCollector, View3D, ViewDrafting, ViewSchedule, ViewSheet

    # Check if this is a drafting view (no 3D model geometry)
    is_drafting = isinstance(view, ViewDrafting)

    # Initialize bounds tracking
    min_x = float('inf')
    min_y = float('inf')
    max_x = float('-inf')
    max_y = float('-inf')
    found_elements = 0

    try:
        if is_drafting:
            # Drafting views: collect 2D annotation elements only
            collector = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()

            for elem in collector:
                # Get bounding box in view coordinates
                try:
                    bbox = elem.get_BoundingBox(view)
                    if bbox is None:
                        continue

                    # Transform to view-local XY
                    min_pt = basis.world_to_view_local(bbox.Min)
                    max_pt = basis.world_to_view_local(bbox.Max)

                    min_x = min(min_x, min_pt[0], max_pt[0])
                    min_y = min(min_y, min_pt[1], max_pt[1])
                    max_x = max(max_x, min_pt[0], max_pt[0])
                    max_y = max(max_y, min_pt[1], max_pt[1])

                    found_elements += 1
                except:
                    continue
        else:
            # Plan/section/elevation: collect 3D model geometry
            collector = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()

            for elem in collector:
                # Get bounding box in view coordinates
                try:
                    bbox = elem.get_BoundingBox(view)
                    if bbox is None:
                        continue

                    # Skip view-specific annotations (not model geometry)
                    try:
                        if bool(getattr(elem, 'ViewSpecific', False)):
                            continue
                    except:
                        pass

                    # Transform to view-local XY
                    min_pt = basis.world_to_view_local(bbox.Min)
                    max_pt = basis.world_to_view_local(bbox.Max)

                    min_x = min(min_x, min_pt[0], max_pt[0])
                    min_y = min(min_y, min_pt[1], max_pt[1])
                    max_x = max(max_x, min_pt[0], max_pt[0])
                    max_y = max(max_y, min_pt[1], max_pt[1])

                    found_elements += 1
                except:
                    continue

    except Exception as e:
        print(f"WARNING: synthetic_bounds_from_visible_extents failed: {e}")

    # If no elements found, use default bounds
    if found_elements == 0 or min_x == float('inf'):
        print(f"WARNING: No elements found for synthetic bounds in view '{view.Name}'. Using default 200x200ft bounds.")
        return Bounds2D(-100.0 - buffer, -100.0 - buffer, 100.0 + buffer, 100.0 + buffer)

    # Add buffer and return
    return Bounds2D(min_x - buffer, min_y - buffer, max_x + buffer, max_y + buffer)
