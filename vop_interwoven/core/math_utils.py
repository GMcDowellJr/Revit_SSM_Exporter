"""
Mathematical utilities for VOP interwoven pipeline.

Provides bounds and rectangle operations used throughout the pipeline.
"""


class Bounds2D:
    """2D axis-aligned bounding box in view XY space.

    Attributes:
        xmin, ymin, xmax, ymax: Bounds coordinates

    Example:
        >>> b = Bounds2D(0.0, 0.0, 10.0, 10.0)
        >>> b.width()
        10.0
        >>> b.contains_point(5.0, 5.0)
        True
    """

    def __init__(self, xmin, ymin, xmax, ymax):
        self.xmin = float(xmin)
        self.ymin = float(ymin)
        self.xmax = float(xmax)
        self.ymax = float(ymax)

    def width(self):
        """Width of bounds."""
        return self.xmax - self.xmin

    def height(self):
        """Height of bounds."""
        return self.ymax - self.ymin

    def area(self):
        """Area of bounds."""
        return self.width() * self.height()

    def contains_point(self, x, y):
        """Check if point (x, y) is inside bounds (inclusive)."""
        return self.xmin <= x <= self.xmax and self.ymin <= y <= self.ymax

    def intersects(self, other):
        """Check if this bounds intersects another Bounds2D."""
        if self.xmax < other.xmin or other.xmax < self.xmin:
            return False
        if self.ymax < other.ymin or other.ymax < self.ymin:
            return False
        return True

    def expand(self, margin):
        """Return new Bounds2D expanded by margin on all sides."""
        return Bounds2D(
            self.xmin - margin, self.ymin - margin, self.xmax + margin, self.ymax + margin
        )

    def __repr__(self):
        return f"Bounds2D({self.xmin:.3f}, {self.ymin:.3f}, {self.xmax:.3f}, {self.ymax:.3f})"


class CellRect:
    """Rectangle in grid cell coordinates.

    Attributes:
        i_min, j_min, i_max, j_max: Cell indices (inclusive)
        width_cells, height_cells: Derived dimensions

    Example:
        >>> rect = CellRect(0, 0, 4, 6)
        >>> rect.width_cells
        5
        >>> rect.height_cells
        7
        >>> rect.empty
        False
    """

    def __init__(self, i_min, j_min, i_max, j_max):
        self.i_min = int(i_min)
        self.j_min = int(j_min)
        self.i_max = int(i_max)
        self.j_max = int(j_max)

        # Derived properties
        self.width_cells = max(0, self.i_max - self.i_min + 1)
        self.height_cells = max(0, self.j_max - self.j_min + 1)
        self.empty = (self.width_cells == 0) or (self.height_cells == 0)

    def cells(self):
        """Generator yielding all (i, j) cell indices in this rectangle."""
        for i in range(self.i_min, self.i_max + 1):
            for j in range(self.j_min, self.j_max + 1):
                yield (i, j)

    def cell_count(self):
        """Total number of cells in rectangle."""
        return self.width_cells * self.height_cells

    # Back-compat with older callsites that treated CellRect like Bounds2D
    # and expected width()/height() methods.
    def width(self):
        return self.width_cells

    def height(self):
        return self.height_cells

    def center_cell(self):
        """Return (i, j) of center cell."""
        i_center = (self.i_min + self.i_max) // 2
        j_center = (self.j_min + self.j_max) // 2
        return (i_center, j_center)

    def __repr__(self):
        return f"CellRect(i={self.i_min}..{self.i_max}, j={self.j_min}..{self.j_max}, {self.width_cells}x{self.height_cells})"

def cellrect_dims(rect):
    """Return (width_cells, height_cells) for any supported CellRect-like object.

    This is the single source of truth for deriving dimensions from a projected
    cell-rectangle, because multiple implementations exist (e.g. i_min/i_max vs
    x0/x1). Returns non-negative ints.

    Supported shapes:
      - core.math_utils.CellRect: i_min/i_max/j_min/j_max or width_cells/height_cells
      - annotation.project_bbox_to_cell_rect CellRect: x0/x1/y0/y1 or width_cells/height_cells
    """
    if rect is None:
        raise ValueError("rect is None")

    w = getattr(rect, "width_cells", None)
    h = getattr(rect, "height_cells", None)
    if isinstance(w, int) and isinstance(h, int):
        return (max(0, w), max(0, h))

    # Inclusive index form
    i_min = getattr(rect, "i_min", None)
    i_max = getattr(rect, "i_max", None)
    j_min = getattr(rect, "j_min", None)
    j_max = getattr(rect, "j_max", None)
    if all(v is not None for v in (i_min, i_max, j_min, j_max)):
        try:
            return (max(0, int(i_max) - int(i_min) + 1), max(0, int(j_max) - int(j_min) + 1))
        except Exception as e:
            raise ValueError("unusable i_min/i_max/j_min/j_max") from e

    # Half-open coordinate form
    x0 = getattr(rect, "x0", None)
    x1 = getattr(rect, "x1", None)
    y0 = getattr(rect, "y0", None)
    y1 = getattr(rect, "y1", None)
    if all(v is not None for v in (x0, x1, y0, y1)):
        try:
            return (max(0, int(x1) - int(x0)), max(0, int(y1) - int(y0)))
        except Exception as e:
            raise ValueError("unusable x0/x1/y0/y1") from e

    raise ValueError("unsupported CellRect-like object")


def rect_intersects_bounds(rect_xmin, rect_ymin, rect_xmax, rect_ymax, bounds):
    """Check if rectangle [xmin, ymin, xmax, ymax] intersects Bounds2D.

    Args:
        rect_xmin, rect_ymin, rect_xmax, rect_ymax: Rectangle coordinates
        bounds: Bounds2D object

    Returns:
        True if rectangles overlap, False otherwise

    Example:
        >>> b = Bounds2D(0.0, 0.0, 10.0, 10.0)
        >>> rect_intersects_bounds(5.0, 5.0, 15.0, 15.0, b)
        True
        >>> rect_intersects_bounds(20.0, 20.0, 30.0, 30.0, b)
        False
    """
    if rect_xmax < bounds.xmin or rect_xmin > bounds.xmax:
        return False
    if rect_ymax < bounds.ymin or rect_ymin > bounds.ymax:
        return False
    return True


def clamp(value, min_val, max_val):
    """Clamp value to [min_val, max_val]."""
    return max(min_val, min(value, max_val))


def point_in_rect(x, y, xmin, ymin, xmax, ymax):
    """Check if point (x, y) is inside rectangle [xmin, ymin, xmax, ymax]."""
    return xmin <= x <= xmax and ymin <= y <= ymax
