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


# -----------------------------------------------------------------------------
# Edge-to-Loop Assembly (Cell-Size-Adaptive)
# -----------------------------------------------------------------------------

def compute_edge_snap_tolerance(raster, cfg=None):
    """
    Compute adaptive edge snapping tolerance based on cell size.

    Tolerance is expressed as percentage of cell size (default 1%).
    This ensures scale-invariance: tolerance adapts to view resolution.

    Args:
        raster: ViewRaster object with cell_size_ft attribute
        cfg: Optional Config object with edge_snap_tolerance_pct

    Returns:
        float: Tolerance in feet (UV space units)

    Examples:
        >>> from .raster import ViewRaster
        >>> from .math_utils import Bounds2D
        >>> bounds = Bounds2D(0, 0, 10, 10)
        >>> raster = ViewRaster(100, 100, 1.0, bounds)
        >>> compute_edge_snap_tolerance(raster)
        0.01

        >>> raster_fine = ViewRaster(1000, 1000, 0.1, bounds)
        >>> compute_edge_snap_tolerance(raster_fine)
        0.001
    """
    # Default: 1% of cell size (sub-pixel, visually imperceptible)
    tolerance_pct = 1.0

    if cfg:
        tolerance_pct = getattr(cfg, 'edge_snap_tolerance_pct', 1.0)

        # Clamp between 0.5% and 5% to prevent extreme values
        min_pct = getattr(cfg, 'edge_snap_tolerance_min_pct', 0.5)
        max_pct = getattr(cfg, 'edge_snap_tolerance_max_pct', 5.0)
        tolerance_pct = max(min_pct, min(max_pct, tolerance_pct))

    tolerance_ft = raster.cell_size_ft * (tolerance_pct / 100.0)

    return tolerance_ft


def signed_polygon_area(points):
    """
    Compute signed area using shoelace formula.

    Convention: CCW winding = positive, CW winding = negative

    Args:
        points: List of (u, v) tuples

    Returns:
        float: Signed area (positive for CCW, negative for CW)
    """
    if len(points) < 3:
        return 0.0

    area = 0.0
    n = len(points)
    for i in range(n):
        j = (i + 1) % n
        area += points[i][0] * points[j][1]
        area -= points[j][0] * points[i][1]
    return area / 2.0


def extract_cycles_from_graph(graph, vertex_coords, cfg=None):
    """
    Find closed cycles in undirected graph using DFS.

    Args:
        graph: dict mapping vertex_id -> [connected_vertex_ids]
        vertex_coords: dict mapping vertex_id -> (u, v)
        cfg: Optional Config object

    Returns:
        List of loop dicts sorted by area (largest first)
    """
    if not graph or not vertex_coords:
        return []

    max_depth = getattr(cfg, 'edge_cycle_max_depth', 1000) if cfg else 1000

    visited_global = set()
    cycles = []

    def dfs_find_cycle(start, current, parent, path, depth):
        """DFS to find cycles. Returns True if cycle found."""
        if depth > max_depth:
            return False

        if current in path:
            # Found cycle
            cycle_start_idx = path.index(current)
            cycle_vertices = path[cycle_start_idx:]

            # Convert to coordinates
            cycle_coords = []
            for v in cycle_vertices:
                if v in vertex_coords:
                    cycle_coords.append(vertex_coords[v])

            if len(cycle_coords) >= 3:
                # Close the loop
                if cycle_coords[0] != cycle_coords[-1]:
                    cycle_coords.append(cycle_coords[0])

                # Compute signed area
                area = signed_polygon_area(cycle_coords)

                cycles.append({
                    'points': cycle_coords,
                    'is_hole': area < 0,  # CW winding = hole
                    'area': abs(area),
                    'strategy': 'edge_assembly'
                })

            return True

        if current in visited_global:
            return False

        visited_global.add(current)
        path.append(current)

        neighbors = graph.get(current, [])
        for neighbor in neighbors:
            if neighbor == parent:
                continue
            if dfs_find_cycle(start, neighbor, current, path, depth + 1):
                path.pop()
                return True

        path.pop()
        return False

    # Try starting from each vertex
    for vertex in list(graph.keys()):
        if vertex not in visited_global:
            dfs_find_cycle(vertex, vertex, None, [], 0)

    # Sort by area (largest first)
    cycles.sort(key=lambda x: x['area'], reverse=True)

    return cycles


def close_gaps_and_retry(graph, vertex_coords, extended_tolerance, cfg=None):
    """
    Heuristic gap closing for nearly-closed loops.

    Strategy:
    1. Find vertices with degree 1 (dangling endpoints)
    2. Connect pairs of endpoints within extended_tolerance
    3. Re-run cycle extraction

    Args:
        graph: dict mapping vertex_id -> [connected_vertex_ids]
        vertex_coords: dict mapping vertex_id -> (u, v)
        extended_tolerance: float (typically 2x base tolerance)
        cfg: Optional Config object

    Returns:
        List of loops or empty list if gap closing failed
    """
    import math

    # Find dangling endpoints (degree 1)
    endpoints = []
    for vertex_id, neighbors in graph.items():
        if len(neighbors) == 1:
            if vertex_id in vertex_coords:
                endpoints.append(vertex_id)

    if len(endpoints) < 2:
        return []

    # Try connecting pairs of endpoints within tolerance
    tol_sq = extended_tolerance * extended_tolerance
    connected_any = False

    for i in range(len(endpoints)):
        for j in range(i + 1, len(endpoints)):
            v1 = endpoints[i]
            v2 = endpoints[j]

            if v1 not in vertex_coords or v2 not in vertex_coords:
                continue

            p1 = vertex_coords[v1]
            p2 = vertex_coords[v2]

            dist_sq = (p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2

            if dist_sq <= tol_sq:
                # Connect these endpoints
                if v1 not in graph:
                    graph[v1] = []
                if v2 not in graph:
                    graph[v2] = []

                if v2 not in graph[v1]:
                    graph[v1].append(v2)
                if v1 not in graph[v2]:
                    graph[v2].append(v1)

                connected_any = True

    if not connected_any:
        return []

    # Re-run cycle extraction
    return extract_cycles_from_graph(graph, vertex_coords, cfg)


def assemble_edge_loops(edges, raster, cfg=None):
    """
    Convert disconnected edges to closed loops with cell-adaptive tolerance.

    Strategy:
    1. Build vertex graph from edge endpoints with snapping (adaptive tolerance)
    2. Extract closed cycles using DFS
    3. If no cycles found, try gap closing with 2x tolerance
    4. Ultimate fallback: convex hull of all vertices

    Args:
        edges: List of edges where each edge has:
               - .start: (u, v) tuple in UV coordinates
               - .end: (u, v) tuple in UV coordinates
               OR list of dicts with 'start' and 'end' keys
        raster: ViewRaster object (provides cell_size_ft)
        cfg: Optional Config object

    Returns:
        List of loop dicts: [
            {
                'points': [(u, v), ...],  # Closed polygon in UV coords
                'is_hole': bool,          # True if CW winding (hole)
                'strategy': str,          # 'edge_assembly', 'gap_closed', or 'convex_hull'
            },
            ...
        ]
        Returns empty list if no valid loops can be formed.

    Commentary:
        - Tolerance is 1% of cell size by default (adaptive to scale)
        - Vertices within tolerance are snapped together
        - Uses DFS to find closed cycles in edge graph
        - Classifies loops as holes based on winding order (signed area)
        - Sorts loops by area (largest first = typically outer boundary)
    """
    import math

    if not edges:
        return []

    # Compute adaptive tolerance
    tolerance = compute_edge_snap_tolerance(raster, cfg)

    # Log tolerance for debugging
    print("[EDGE_ASSEMBLY] View cell_size={:.3f}ft, tolerance={:.6f}ft ({:.1f}%)".format(
        raster.cell_size_ft,
        tolerance,
        (tolerance / raster.cell_size_ft) * 100.0
    ))

    # Build vertex graph with snapping
    tolerance_sq = tolerance * tolerance
    vertex_map = {}  # Maps snapped coordinate tuple to vertex ID
    vertex_coords = {}  # Maps vertex ID to coordinate
    graph = {}  # Maps vertex ID to list of connected vertex IDs
    next_vertex_id = 0

    def snap_point(p):
        """Snap point to existing vertex or create new one."""
        nonlocal next_vertex_id

        # Convert to (u, v) if it's (u, v, w)
        u, v = p[0], p[1]

        # Find existing vertex within tolerance
        for vid, coord in vertex_coords.items():
            dist_sq = (coord[0] - u) ** 2 + (coord[1] - v) ** 2
            if dist_sq <= tolerance_sq:
                return vid

        # Create new vertex
        vid = next_vertex_id
        next_vertex_id += 1
        vertex_coords[vid] = (u, v)
        graph[vid] = []
        return vid

    # Process edges
    edge_count = 0
    for edge in edges:
        # Handle both object attributes and dict keys
        if hasattr(edge, 'start'):
            start = edge.start
            end = edge.end
        elif isinstance(edge, dict):
            start = edge.get('start')
            end = edge.get('end')
        else:
            continue

        if not start or not end:
            continue

        # Skip zero-length edges
        dist_sq = (end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2
        if dist_sq < 1e-18:
            continue

        # Snap endpoints to vertices
        v1 = snap_point(start)
        v2 = snap_point(end)

        # Add edge to graph (undirected)
        if v2 not in graph[v1]:
            graph[v1].append(v2)
        if v1 not in graph[v2]:
            graph[v2].append(v1)

        edge_count += 1

    if edge_count == 0:
        return []

    # Extract cycles
    loops = extract_cycles_from_graph(graph, vertex_coords, cfg)

    if loops:
        print("[EDGE_ASSEMBLY] Result: {} loops from {} edges (strategy: edge_assembly)".format(
            len(loops), edge_count
        ))
        return loops

    # Try gap closing
    gap_multiplier = getattr(cfg, 'edge_gap_closing_multiplier', 2.0) if cfg else 2.0
    extended_tolerance = tolerance * gap_multiplier

    print("[EDGE_ASSEMBLY] No cycles found, trying gap closing with tolerance={:.6f}ft".format(
        extended_tolerance
    ))

    loops = close_gaps_and_retry(graph, vertex_coords, extended_tolerance, cfg)

    if loops:
        for loop in loops:
            loop['strategy'] = 'gap_closed'
        print("[EDGE_ASSEMBLY] Result: {} loops from {} edges (strategy: gap_closed)".format(
            len(loops), edge_count
        ))
        return loops

    # Ultimate fallback: convex hull
    print("[EDGE_ASSEMBLY] Gap closing failed, falling back to convex hull")

    if not vertex_coords:
        return []

    # Get all vertex coordinates
    all_points = list(vertex_coords.values())

    # Import convex hull from silhouette module
    try:
        from .silhouette import _convex_hull_2d
        hull_points = _convex_hull_2d(all_points)

        if len(hull_points) >= 3:
            loops = [{
                'points': hull_points,
                'is_hole': False,
                'strategy': 'convex_hull'
            }]
            print("[EDGE_ASSEMBLY] Result: 1 loop from {} edges (strategy: convex_hull)".format(
                edge_count
            ))
            return loops
    except Exception as e:
        print("[EDGE_ASSEMBLY] Convex hull failed: {}".format(e))

    return []
