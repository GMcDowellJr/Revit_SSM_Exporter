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

    def center_cell(self):
        """Return (i, j) of center cell."""
        i_center = (self.i_min + self.i_max) // 2
        j_center = (self.j_min + self.j_max) // 2
        return (i_center, j_center)

    def __repr__(self):
        return f"CellRect(i={self.i_min}..{self.i_max}, j={self.j_min}..{self.j_max}, {self.width_cells}x{self.height_cells})"


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
