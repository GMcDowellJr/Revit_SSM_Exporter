"""
Geometry classification and proxy generation for VOP interwoven pipeline.

Provides UV-based element classification (TINY/LINEAR/AREAL) and proxy
generation (UV_AABB/OBB) for lightweight occlusion handling.
"""

from enum import Enum


class Mode(Enum):
    """Element classification based on UV footprint size.

    TINY: Both dimensions <= tiny_max (e.g., 2x2 cells or smaller)
    LINEAR: One dimension <= thin_max, other > thin_max (e.g., 1x10 cells)
    AREAL: Both dimensions > thin_max (e.g., 10x10 cells or larger)
    """

    TINY = 1
    LINEAR = 2
    AREAL = 3

def _mesh_vertex_count(mesh):
    """Best-effort vertex count for Revit Mesh-like objects.

    Must not throw. Early-out uses this for heuristics only.
    """
    if mesh is None:
        return 0
    try:
        verts = getattr(mesh, "Vertices", None)
        if verts is not None:
            try:
                return len(verts)
            except Exception:
                # Some Revit collections expose Size instead of __len__
                size = getattr(verts, "Size", None)
                if isinstance(size, int):
                    return size
    except Exception:
        pass

    try:
        n = getattr(mesh, "NumVertices", None)
        if isinstance(n, int):
            return n
    except Exception:
        pass

    return 0
    
def tier_a_is_ambiguous(minor_cells, aabb_area_cells, grid_area, cell_size_world, cfg):
    t = cfg.thin_max  # required t=2 unless cfg changes
    margin_cells = max(cfg.tierb_margin_cells_min,
                       min(int(round(cell_size_world / cfg.tierb_cell_size_ref_ft)),
                           cfg.tierb_margin_cells_max))
    thickness_ambig = (t < minor_cells <= (t + margin_cells))

    area_thresh = max(cfg.tierb_area_thresh_min,
                      min(int(round(cfg.tierb_area_fraction * grid_area)),
                          cfg.tierb_area_thresh_max))
    area_ambig = (aabb_area_cells >= area_thresh)

    return thickness_ambig or area_ambig


def classify_by_uv_pca(points_uv, cfg, cell_size_uv=1.0):
    from .pca2d import pca_oriented_extents_uv
    major, minor = pca_oriented_extents_uv(points_uv)
    major_cells = major / cell_size_uv
    minor_cells = minor / cell_size_uv

    if minor_cells <= cfg.tiny_max and major_cells <= cfg.tiny_max:
        return Mode.TINY
    if minor_cells <= cfg.thin_max:
        return Mode.LINEAR
    return Mode.AREAL

def classify_by_uv(u, v, cfg):
    """Classify element mode based on UV cell dimensions.

    Args:
        u: Width in grid cells
        v: Height in grid cells
        cfg: Config object with tiny_max and thin_max thresholds

    Returns:
        Mode enum (TINY, LINEAR, or AREAL)

    Commentary:
        ✔ Tiny if U<=tiny_max AND V<=tiny_max (equivalent to U<tiny_max+1 & V<tiny_max+1)
        ✔ Linear if min(U,V)<=thin_max AND max(U,V)>thin_max
        ✔ Areal otherwise (large in both dimensions)

    Examples:
        >>> from ..config import Config
        >>> cfg = Config(tiny_max=2, thin_max=2)
        >>> classify_by_uv(1, 1, cfg)
        <Mode.TINY: 1>
        >>> classify_by_uv(1, 10, cfg)
        <Mode.LINEAR: 2>
        >>> classify_by_uv(10, 10, cfg)
        <Mode.AREAL: 3>
    """
    if u <= cfg.tiny_max and v <= cfg.tiny_max:
        return Mode.TINY

    min_dim = min(u, v)
    max_dim = max(u, v)

    if min_dim <= cfg.thin_max and max_dim > cfg.thin_max:
        return Mode.LINEAR

    return Mode.AREAL


class UV_AABB:
    """Axis-aligned bounding box proxy in UV (view XY) space.

    Used for TINY elements - simplest proxy representation.

    Attributes:
        u_min, v_min, u_max, v_max: Bounds in view-local coordinates
        rect: CellRect representation (optional)

    Example:
        >>> from .math_utils import CellRect
        >>> rect = CellRect(0, 0, 1, 1)
        >>> proxy = make_uv_aabb(rect)
        >>> proxy.width()
        2
    """

    def __init__(self, u_min, v_min, u_max, v_max, rect=None):
        self.u_min = float(u_min)
        self.v_min = float(v_min)
        self.u_max = float(u_max)
        self.v_max = float(v_max)
        self.rect = rect  # Optional CellRect for cell-based operations

    def width(self):
        """Width of AABB."""
        return self.u_max - self.u_min

    def height(self):
        """Height of AABB."""
        return self.v_max - self.v_min

    def center(self):
        """Center point (u, v)."""
        return ((self.u_min + self.u_max) * 0.5, (self.v_min + self.v_max) * 0.5)

    def edges(self):
        """Return 4 edge segments [(u0,v0), (u1,v1)] for stamping."""
        corners = [
            (self.u_min, self.v_min),
            (self.u_max, self.v_min),
            (self.u_max, self.v_max),
            (self.u_min, self.v_max),
        ]
        edges = []
        for i in range(4):
            p0 = corners[i]
            p1 = corners[(i + 1) % 4]
            edges.append((p0, p1))
        return edges

    def __repr__(self):
        return f"UV_AABB(u={self.u_min:.2f}..{self.u_max:.2f}, v={self.v_min:.2f}..{self.v_max:.2f})"


class OBB:
    """Oriented bounding box proxy for LINEAR elements.

    Captures orientation of long skinny elements (doors, walls, etc.)
    for better occlusion representation than axis-aligned boxes.

    Attributes:
        center: (u, v) center point
        axes: [(ax_u, ax_v), (ay_u, ay_v)] - two orthogonal unit vectors
        extents: (half_width, half_height) along axes
        rect: CellRect representation (optional)

    Example:
        >>> # Door: 1ft wide x 10ft tall, oriented along +V axis
        >>> obb = OBB(
        ...     center=(5.0, 5.0),
        ...     axes=[(1.0, 0.0), (0.0, 1.0)],
        ...     extents=(0.5, 5.0)
        ... )
        >>> obb.long_axis_length()
        10.0
    """

    def __init__(self, center, axes, extents, rect=None):
        self.center = tuple(center)  # (u, v)
        self.axes = [tuple(ax) for ax in axes]  # [(ax_u, ax_v), (ay_u, ay_v)]
        self.extents = tuple(extents)  # (half_width, half_height)
        self.rect = rect

    def long_axis_length(self):
        """Length along the long axis (2 * max extent)."""
        return 2.0 * max(self.extents)

    def short_axis_length(self):
        """Length along the short axis (2 * min extent)."""
        return 2.0 * min(self.extents)

    def corners(self):
        """Return 4 corner points for stamping edges."""
        cx, cy = self.center
        ax_u, ax_v = self.axes[0]
        ay_u, ay_v = self.axes[1]
        ex, ey = self.extents

        # Four corners: center ± ex*axis[0] ± ey*axis[1]
        corners = []
        for sx in [-1, 1]:
            for sy in [-1, 1]:
                u = cx + sx * ex * ax_u + sy * ey * ay_u
                v = cy + sx * ex * ax_v + sy * ey * ay_v
                corners.append((u, v))
        return corners

    def edges(self):
        """Return 4 edge segments [(u0,v0), (u1,v1)] for stamping."""
        c = self.corners()
        # Order: bottom-left, bottom-right, top-right, top-left
        edges = [
            (c[0], c[1]),  # bottom
            (c[1], c[3]),  # right
            (c[3], c[2]),  # top
            (c[2], c[0]),  # left
        ]
        return edges

    def __repr__(self):
        return f"OBB(center={self.center}, extents={self.extents})"


def make_uv_aabb(rect):
    """Create UV_AABB proxy from CellRect.

    Args:
        rect: CellRect with cell indices

    Returns:
        UV_AABB proxy

    Example:
        >>> from .math_utils import CellRect
        >>> rect = CellRect(0, 0, 2, 2)
        >>> proxy = make_uv_aabb(rect)
        >>> proxy.width()
        3
    """
    # Convert cell indices to continuous UV bounds
    # Cells are [i_min, i_max] x [j_min, j_max] inclusive
    u_min = float(rect.i_min)
    u_max = float(rect.i_max + 1)  # +1 for exclusive upper bound
    v_min = float(rect.j_min)
    v_max = float(rect.j_max + 1)

    return UV_AABB(u_min, v_min, u_max, v_max, rect=rect)


def make_obb_or_skinny_aabb(elem, transform, rect, view, raster):
    """Create OBB proxy for LINEAR elements using precomputed OBB from collection.

    For LINEAR elements (one dimension thin, one long), construct an oriented
    bounding box aligned with the element's dominant axis. Uses OBB data
    precomputed by _pca_obb_uv() during collection phase to avoid redundant PCA.

    Args:
        elem: Revit element
        transform: World transform (identity for host, link transform for linked)
        rect: CellRect footprint (enriched with obb_data by collection.py)
        view: Revit View
        raster: ViewRaster (for coordinate transforms)

    Returns:
        OBB proxy with proper orientation, or UV_AABB for degenerate cases

    Commentary:
        ✔ Preserves diagonal orientation for walls, doors, thin elements
        ✔ Uses precomputed PCA from collection phase (zero redundant computation)
        ✔ Only falls back to AABB for truly degenerate geometry (near-zero area)
    """
    import math

    # Extract precomputed OBB data from collection phase
    obb_data = getattr(rect, 'obb_data', None)

    if obb_data is None:
        # Fallback: no OBB data available (shouldn't happen in normal flow)
        # This means either _pca_obb_uv() failed or rect wasn't enriched
        return make_uv_aabb(rect)

    obb_corners = obb_data['obb_corners']  # 4 corners of fitted OBB in UV space

    # Compute OBB center (centroid of 4 corners)
    center_u = sum(pt[0] for pt in obb_corners) / 4.0
    center_v = sum(pt[1] for pt in obb_corners) / 4.0

    # Reconstruct axes from OBB rectangle geometry.
    # _pca_obb_uv() returns corners ordered as: [p0, p1, p2, p3]
    # where p0→p1 and p0→p3 are the two orthogonal edges.
    edge1_u = obb_corners[1][0] - obb_corners[0][0]
    edge1_v = obb_corners[1][1] - obb_corners[0][1]
    len1 = math.sqrt(edge1_u**2 + edge1_v**2)

    edge2_u = obb_corners[3][0] - obb_corners[0][0]
    edge2_v = obb_corners[3][1] - obb_corners[0][1]
    len2 = math.sqrt(edge2_u**2 + edge2_v**2)

    # Degenerate check: near-zero edge lengths indicate collapsed geometry
    if len1 < 0.001 or len2 < 0.001:
        return make_uv_aabb(rect)

    # Normalize to unit axes
    axis1 = (edge1_u / len1, edge1_v / len1)
    axis2 = (edge2_u / len2, edge2_v / len2)

    # Construct OBB proxy
    return OBB(
        center=(center_u, center_v),
        axes=[axis1, axis2],
        extents=(len1 * 0.5, len2 * 0.5),
        rect=rect
    )


def mark_rect_center_cell(rect, mask):
    """Mark center cell of rectangle in boolean mask (for TINY proxy presence).

    Args:
        rect: CellRect
        mask: List/array representing grid cells (modified in-place)

    Returns:
        None (modifies mask in-place)

    Example:
        >>> from .math_utils import CellRect
        >>> mask = [False] * 100  # 10x10 grid
        >>> rect = CellRect(2, 2, 4, 4)  # 3x3 rect
        >>> # Mark center cell (i=3, j=3) -> index 3*10+3 = 33
        >>> # (actual implementation would use raster.W for indexing)
    """
    i_center, j_center = rect.center_cell()
    # Note: Actual implementation needs raster.W to compute index
    # This is a stub - real implementation in raster module
    pass


def mark_thin_band_along_long_axis(rect, mask):
    """Mark thin band along long axis of rectangle (for LINEAR proxy presence).

    Args:
        rect: CellRect
        mask: List/array representing grid cells (modified in-place)

    Returns:
        None (modifies mask in-place)

    Commentary:
        ⚠ This is a placeholder - real implementation requires:
           - Determination of long axis (horizontal vs vertical)
           - 1-cell-wide band marking along that axis
    """
    # TODO: Implement thin band marking
    # For now, this is a placeholder
    pass
