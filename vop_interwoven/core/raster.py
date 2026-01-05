"""
Raster data structures for VOP interwoven pipeline.

Provides ViewRaster and TileMap classes for tracking occlusion state,
depth buffers, and edge/annotation layers per view.
"""


def _extract_source_type(doc_key):
    """Extract simple source type from doc_key.

    Args:
        doc_key: Source key ("HOST", "RVT_LINK:...", "DWG_IMPORT:...")

    Returns:
        "HOST", "LINK", or "DWG"
    """
    if not doc_key:
        return "HOST"
    if doc_key.startswith("RVT_LINK:"):
        return "LINK"
    elif doc_key.startswith("DWG_IMPORT:") or doc_key.startswith("DWG_") or doc_key.startswith("DXF_"):
        return "DWG"
    else:
        return "HOST"


class TileMap:
    """Tile-based spatial acceleration structure for early-out occlusion testing.

    Attributes:
        tile_size: Size of each tile in cells (e.g., 16x16)
        tiles_x, tiles_y: Number of tiles in X and Y dimensions
        filled_count: List of filled cell counts per tile
        w_min_tile: List of minimum W-depth values per tile (view-space depth)

    Example:
        >>> tm = TileMap(tile_size=16, width=64, height=64)
        >>> tm.tiles_x, tm.tiles_y
        (4, 4)
        >>> tm.get_tile_index(0, 0)
        0
        >>> tm.get_tile_index(16, 16)
        5
    """

    def __init__(self, tile_size, width, height):
        """Initialize tile map.

        Args:
            tile_size: Size of each tile in grid cells
            width: Grid width in cells
            height: Grid height in cells
        """
        self.tile_size = int(tile_size)
        self.tiles_x = (width + tile_size - 1) // tile_size
        self.tiles_y = (height + tile_size - 1) // tile_size
        num_tiles = self.tiles_x * self.tiles_y

        # Per-tile statistics for early-out testing
        self.filled_count = [0] * num_tiles  # Count of filled cells in tile
        self.w_min_tile = [float("inf")] * num_tiles  # Minimum W-depth in tile (view-space depth)

    def get_tile_index(self, cell_i, cell_j):
        """Get tile index for cell (i, j).

        Args:
            cell_i: Cell column index
            cell_j: Cell row index

        Returns:
            Tile index (0-based)
        """
        tile_i = cell_i // self.tile_size
        tile_j = cell_j // self.tile_size
        return tile_j * self.tiles_x + tile_i

    def get_tiles_for_rect(self, i_min, j_min, i_max, j_max):
        """Get list of tile indices overlapping rectangle.

        Args:
            i_min, j_min, i_max, j_max: Cell rectangle bounds (inclusive)

        Returns:
            List of tile indices
        """
        tile_i_min = i_min // self.tile_size
        tile_i_max = i_max // self.tile_size
        tile_j_min = j_min // self.tile_size
        tile_j_max = j_max // self.tile_size

        tiles = []
        for tj in range(tile_j_min, tile_j_max + 1):
            for ti in range(tile_i_min, tile_i_max + 1):
                if 0 <= ti < self.tiles_x and 0 <= tj < self.tiles_y:
                    tiles.append(tj * self.tiles_x + ti)
        return tiles

    def is_tile_full(self, tile_idx):
        """Check if tile is completely filled.

        Args:
            tile_idx: Tile index

        Returns:
            True if all cells in tile are filled
        """
        cells_per_tile = self.tile_size * self.tile_size
        return self.filled_count[tile_idx] >= cells_per_tile

    def update_filled_count(self, cell_i, cell_j, increment=1):
        """Update filled count for tile containing cell.

        Args:
            cell_i, cell_j: Cell indices
            increment: Amount to add to filled count (default: 1)
        """
        tile_idx = self.get_tile_index(cell_i, cell_j)
        if 0 <= tile_idx < len(self.filled_count):
            self.filled_count[tile_idx] += increment

    def update_w_min(self, cell_i, cell_j, depth):
        """Update minimum W-depth for tile containing cell.

        Args:
            cell_i, cell_j: Cell indices
            depth: W-depth value to compare
        """
        tile_idx = self.get_tile_index(cell_i, cell_j)
        if 0 <= tile_idx < len(self.w_min_tile):
            if depth < self.w_min_tile[tile_idx]:
                self.w_min_tile[tile_idx] = depth


class ViewRaster:
    """Raster representation of a single view for VOP interwoven pipeline.

    Stores all occlusion state, depth buffers, edge layers, and annotation
    data for one Revit view.

    Attributes:
        W, H: Raster dimensions in cells
        cell_size_ft: Cell size in model units (feet)
        bounds_xy: Bounds2D in view-local XY

        # Global per-cell occlusion depth buffer (W-depth from view-space UVW)
        w_occ: Float array [W*H] - nearest W-depth per cell (+inf if empty)
        tile: TileMap for early-out testing

        # Per-source occupancy layers (depth-tested, only mark when depth wins)
        occ_host: Boolean array [W*H] - host document element occupancy
        occ_link: Boolean array [W*H] - linked RVT element occupancy
        occ_dwg: Boolean array [W*H] - DWG/DXF import occupancy

        # Legacy model presence (unified, for backward compatibility)
        model_mask: Boolean array [W*H] - interior coverage

        # Edge rasters
        model_edge_key: Int array [W*H] - depth-tested visible edges (AreaL)
        model_proxy_key: Int array [W*H] - proxy edges (Tiny/Linear)

        # Optional proxy presence
        model_proxy_mask: Boolean array [W*H] - minimal presence for Tiny/Linear

        # Annotation
        anno_key: Int array [W*H] - annotation edges from 2D exporter
        anno_over_model: Boolean array [W*H] - derived from anno && model presence

        # Metadata
        element_meta_index_by_key: Dict[key -> index]
        element_meta: List of element metadata dicts
        anno_meta_index_by_key: Dict[key -> index]
        anno_meta: List of annotation metadata dicts

        # Depth test statistics
        depth_test_attempted: Count of attempted cell writes
        depth_test_wins: Count of writes that won depth test
        depth_test_rejects: Count of writes rejected by depth test

    Example:
        >>> from .math_utils import Bounds2D
        >>> bounds = Bounds2D(0.0, 0.0, 100.0, 100.0)
        >>> raster = ViewRaster(width=64, height=64, cell_size=1.0, bounds=bounds, tile_size=16)
        >>> raster.W, raster.H
        (64, 64)
        >>> raster.try_write_cell(10, 10, w_depth=5.0, source="HOST")
        True
        >>> raster.w_occ[10 * 64 + 10]
        5.0
    """

    def rasterize_open_polylines(self, polylines, key_index, depth=0.0, source="HOST"):
        """Rasterize OPEN polyline paths as edges only (no interior fill).

        Args:
            polylines: List of polyline dicts with {'points': [(u,v), ...], 'open': True}
            key_index: Element metadata index (for edge tracking)
            depth: W-depth value for occlusion testing (default: 0.0)
            source: Source type - "HOST", "LINK", or "DWG" (default: "HOST")

        Returns:
            Number of edge cells stamped

        Commentary:
            - Used for DWG/DXF curves and other open paths
            - Stamps edges only, no interior fill
            - Updates model_edge_key and contributes to w_occ occlusion
        """
        filled = 0

        for pl in polylines:
            pts = pl.get("points", [])
            if not pts or len(pts) < 2:
                continue

            # Convert UV floats -> ij ints
            pts_ij = []
            for pt in pts:
                # Handle both 2-tuples (u, v) and 3-tuples (u, v, w)
                u, v = pt[0], pt[1]
                i = int((u - self.bounds.xmin) / self.cell_size)
                j = int((v - self.bounds.ymin) / self.cell_size)
                if 0 <= i < self.W and 0 <= j < self.H:
                    pts_ij.append((i, j))

            if len(pts_ij) < 2:
                continue

            # Draw segments
            for k in range(len(pts_ij) - 1):
                i0, j0 = pts_ij[k]
                i1, j1 = pts_ij[k + 1]
                for (ii, jj) in _bresenham_line(i0, j0, i1, j1):
                    idx = self.get_cell_index(ii, jj)
                    if idx is None:
                        continue

                    # edge presence - check current occlusion depth
                    w_here = self.w_occ[idx]

                    # Only stamp the edge if this element is nearer than what's already there
                    if w_here == float("inf") or depth <= w_here:
                        self.model_edge_key[idx] = key_index

                        # Contribute to occlusion along the curve using try_write_cell
                        # This ensures DWG curves participate in depth testing
                        if depth < w_here:
                            self.try_write_cell(ii, jj, w_depth=depth, source=source)
                    filled += 1

        return filled
    
    def __init__(self, width, height, cell_size, bounds, tile_size=16):
        """Initialize view raster.

        Args:
            width: Raster width in cells
            height: Raster height in cells
            cell_size: Cell size in model units (feet)
            bounds: Bounds2D in view-local XY
            tile_size: Tile size for acceleration structure
        """
        self.W = int(width)
        self.H = int(height)
        self.cell_size_ft = float(cell_size)
        self.bounds_xy = bounds

        N = self.W * self.H

        # Global per-cell occlusion depth buffer (W-depth from view-space UVW)
        self.w_occ = [float("inf")] * N

        # Tile acceleration
        self.tile = TileMap(tile_size, self.W, self.H)

        # Per-source occupancy layers (depth-tested)
        self.occ_host = [False] * N
        self.occ_link = [False] * N
        self.occ_dwg = [False] * N

        # Legacy model presence (unified, for backward compatibility)
        self.model_mask = [False] * N

        # Edge rasters
        self.model_edge_key = [-1] * N
        self.model_proxy_key = [-1] * N

        # Proxy presence (optional)
        self.model_proxy_mask = [False] * N

        # Annotation
        self.anno_key = [-1] * N
        self.anno_over_model = [False] * N

        # Metadata tracking
        self.element_meta_index_by_key = {}
        self.element_meta = []
        self.anno_meta_index_by_key = {}
        self.anno_meta = []

        # Depth test statistics
        self.depth_test_attempted = 0
        self.depth_test_wins = 0
        self.depth_test_rejects = 0

    @property
    def width(self):
        """Raster width in cells (alias for W)."""
        return self.W

    @property
    def height(self):
        """Raster height in cells (alias for H)."""
        return self.H

    @property
    def cell_size(self):
        """Cell size in model units (alias for cell_size_ft)."""
        return self.cell_size_ft

    @property
    def bounds(self):
        """Bounds in view-local XY (alias for bounds_xy)."""
        return self.bounds_xy

    def get_cell_index(self, i, j):
        """Get linear index for cell (i, j).

        Args:
            i: Column index (0-based)
            j: Row index (0-based)

        Returns:
            Linear index for cell, or None if out of bounds
        """
        if 0 <= i < self.W and 0 <= j < self.H:
            return j * self.W + i
        return None

    def try_write_cell(self, i, j, w_depth, source):
        """Centralized cell write with depth testing (MANDATORY contract).

        This is the ONLY function that should write to w_occ and occupancy layers.
        All rasterization code must route writes through this function.

        Args:
            i, j: Cell indices (column, row)
            w_depth: W-depth from view-space UVW transform (depth = dot(p - O, forward))
            source: Source identifier ("HOST", "LINK", or "DWG")

        Returns:
            True if depth won and cell was updated, False otherwise

        Behavior:
            1. Compare w_depth to w_occ[u,v]
            2. If nearer (w_depth < w_occ):
                - Update w_occ
                - Mark exactly ONE occupancy layer (occ_host, occ_link, or occ_dwg)
                - Update model_mask (unified legacy layer)
                - Update tile acceleration
                - Increment depth_test_wins
            3. If not nearer:
                - Do nothing
                - Increment depth_test_rejects
            4. Always increment depth_test_attempted

        Commentary:
            This enforces the depth-tested occupancy contract:
            - Behind geometry never marks occupancy
            - Per-source layers only mark winning depth
            - All sources share the same w_occ buffer
        """
        idx = self.get_cell_index(i, j)
        if idx is None:
            return False

        self.depth_test_attempted += 1

        # Depth test: is this element nearer than what's already there?
        if w_depth < self.w_occ[idx]:
            # Check if this is first write to cell (for tile filled count)
            was_empty = self.w_occ[idx] == float("inf")

            # Depth wins - update occlusion and occupancy
            self.w_occ[idx] = w_depth
            self.model_mask[idx] = True

            # Mark exactly one occupancy layer based on source
            if source == "HOST":
                self.occ_host[idx] = True
            elif source == "LINK":
                self.occ_link[idx] = True
            elif source == "DWG":
                self.occ_dwg[idx] = True

            # Update tile acceleration
            self.tile.update_w_min(i, j, w_depth)
            if was_empty:
                self.tile.update_filled_count(i, j, increment=1)

            self.depth_test_wins += 1
            return True
        else:
            # Depth rejected - element is behind existing geometry
            self.depth_test_rejects += 1
            return False

    def set_cell_filled(self, i, j, depth=None):
        """Mark cell as filled with optional depth.

        DEPRECATED: Use try_write_cell() instead for depth-tested writes.
        This method is kept for backward compatibility only.

        Args:
            i, j: Cell indices
            depth: Optional depth value (updates w_occ if provided and nearer)

        Returns:
            True if cell was updated, False if out of bounds
        """
        idx = self.get_cell_index(i, j)
        if idx is None:
            return False

        was_empty = not self.model_mask[idx]

        self.model_mask[idx] = True

        if depth is not None:
            if depth < self.w_occ[idx]:
                self.w_occ[idx] = depth
                self.tile.update_w_min(i, j, depth)
            # DEBUG: Log if depth wasn't updated
            elif getattr(self, '_debug_depth_log_count', 0) < 5:
                self._debug_depth_log_count = getattr(self, '_debug_depth_log_count', 0) + 1
                print("[DEBUG] set_cell_filled({0},{1}): depth={2} NOT < w_occ[{3}]={4}".format(
                    i, j, depth, idx, self.w_occ[idx]))

        if was_empty:
            self.tile.update_filled_count(i, j, increment=1)

        return True

    def get_or_create_element_meta_index(self, elem_id, category, source="HOST", source_label=None):
        """Get or create metadata index for element.

        Args:
            elem_id: Revit element ID (integer)
            category: Element category name (string)
            source: Unique source key for indexing ("HOST", "RVT_LINK:{uid}:{id}", etc.)
            source_label: Optional friendly label for display (defaults to source)

        Returns:
            Integer index for this element's metadata

        Commentary:
            source: Used as unique key for indexing (must be unique per element)
            source_label: Used for display/logging (can be friendly, non-unique)
        """
        key = (elem_id, source)
        if key in self.element_meta_index_by_key:
            return self.element_meta_index_by_key[key]

        idx = len(self.element_meta)
        self.element_meta_index_by_key[key] = idx
        self.element_meta.append(
            {
                "elem_id": elem_id,
                "category": category,
                "source": source,
                "source_label": source_label if source_label is not None else source
            }
        )
        return idx

    def get_or_create_anno_meta_index(self, anno_id, anno_type="TEXT"):
        """Get or create metadata index for annotation.

        Args:
            anno_id: Annotation element ID (integer)
            anno_type: Annotation type ("TEXT", "DIM", "TAG", etc.)

        Returns:
            Integer index for this annotation's metadata
        """
        if anno_id in self.anno_meta_index_by_key:
            return self.anno_meta_index_by_key[anno_id]

        idx = len(self.anno_meta)
        self.anno_meta_index_by_key[anno_id] = idx
        self.anno_meta.append({"anno_id": anno_id, "type": anno_type})
        return idx

    def finalize_anno_over_model(self, cfg):
        """Derive anno_over_model layer from anno_key and model presence.

        Args:
            cfg: Config object (controls whether proxies count as model presence)

        Returns:
            None (updates anno_over_model in-place)

        Commentary:
            ✔ If cfg.over_model_includes_proxies is True, presence = modelMask OR modelProxyMask
            ✔ Otherwise, presence = modelMask only (AreaL occluders)
        """
        for i in range(len(self.anno_key)):
            has_anno = self.anno_key[i] != -1

            if cfg.over_model_includes_proxies:
                has_model = self.model_mask[i] or self.model_proxy_mask[i]
            else:
                has_model = self.model_mask[i]

            self.anno_over_model[i] = has_anno and has_model

    def rasterize_silhouette_loops(self, loops, key_index, depth=0.0, source="HOST"):
        """Rasterize element silhouette loops into model layers with depth testing.

        Args:
            loops: List of loop dicts from silhouette.get_element_silhouette()
                   Each loop has: {'points': [(u, v), ...], 'is_hole': bool}
            key_index: Element metadata index (for edge tracking)
            depth: W-depth value for occlusion testing (default: 0.0)
            source: Source type - "HOST", "LINK", or "DWG" (default: "HOST")

        Returns:
            Number of cells filled

        Commentary:
            - Converts loop points from view UV to cell indices
            - Rasterizes loop edges using Bresenham line algorithm
            - Fills interior using depth-tested scanline fill (try_write_cell)
            - Updates w_occ, per-source occupancy, and model_edge_key
        """
        if not loops:
            return 0

        filled_count = 0

        for loop in loops:
            points_uv = loop.get('points', [])
            is_hole = loop.get('is_hole', False)

            if len(points_uv) < 3:
                continue

            # Convert UV points to cell indices
            points_ij = []
            for pt in points_uv:
                # Handle both 2-tuples (u, v) and 3-tuples (u, v, w)
                u, v = pt[0], pt[1]
                i = int((u - self.bounds_xy.xmin) / self.cell_size_ft)
                j = int((v - self.bounds_xy.ymin) / self.cell_size_ft)

                # Clamp to bounds
                i = max(0, min(i, self.W - 1))
                j = max(0, min(j, self.H - 1))

                points_ij.append((i, j))

            # Rasterize edges
            # 1) Fill interior FIRST (writes w_occ and per-source occupancy for occlusion)
            if not is_hole:
                filled_count += self._scanline_fill(points_ij, key_index, depth, source)

            # 2) Then rasterize edges with depth-test against updated w_occ buffer
            for k in range(len(points_ij) - 1):
                i0, j0 = points_ij[k]
                i1, j1 = points_ij[k + 1]

                for i, j in _bresenham_line(i0, j0, i1, j1):
                    idx = self.get_cell_index(i, j)
                    if idx is None:
                        continue

                    w_here = self.w_occ[idx]
                    if w_here == float("inf") or depth <= w_here:
                        self.model_edge_key[idx] = key_index

        return filled_count

    def _scanline_fill(self, points_ij, key_index, depth, source):
        """Fill polygon interior using scanline algorithm with depth testing.

        Args:
            points_ij: List of (i, j) cell coordinates forming closed polygon
            key_index: Element metadata index
            depth: W-depth value for occlusion testing
            source: Source type ("HOST", "LINK", or "DWG")

        Returns:
            Number of cells filled

        Commentary:
            - Uses try_write_cell for depth-tested occlusion
            - Sets w_occ and per-source occupancy for OCCLUSION (interior blocks visibility)
            - Does NOT set model_edge_key (only boundary marks occupancy)
        """
        if len(points_ij) < 3:
            return 0

        filled = 0

        # Find vertical extent
        j_coords = [j for i, j in points_ij]
        j_min = min(j_coords)
        j_max = max(j_coords)

        # For each scanline
        for j in range(j_min, j_max + 1):
            # Find intersections with polygon edges
            intersections = []

            for k in range(len(points_ij) - 1):
                i0, j0 = points_ij[k]
                i1, j1 = points_ij[k + 1]

                # Skip horizontal edges
                if j0 == j1:
                    continue

                # Check if scanline intersects this edge
                if min(j0, j1) <= j <= max(j0, j1):
                    # Compute intersection i coordinate
                    if j1 != j0:
                        t = float(j - j0) / float(j1 - j0)
                        i_intersect = int(i0 + t * (i1 - i0))
                        intersections.append(i_intersect)

            # Sort intersections
            intersections.sort()

            # Fill between pairs
            for k in range(0, len(intersections) - 1, 2):
                i_start = intersections[k]
                i_end = intersections[k + 1] if k + 1 < len(intersections) else intersections[k]

                for i in range(i_start, i_end + 1):
                    # Use try_write_cell for depth-tested occlusion
                    # Interior fills space and blocks visibility via per-source occupancy
                    if self.try_write_cell(i, j, w_depth=depth, source=source):
                        filled += 1

        return filled

    def to_dict(self):
        """Export raster to dictionary for JSON serialization.

        Returns:
            Dictionary with all raster data (can be large - consider compression)
        """
        return {
            "width": self.W,
            "height": self.H,
            "cell_size_ft": self.cell_size_ft,
            "bounds_xy": {
                "xmin": self.bounds_xy.xmin,
                "ymin": self.bounds_xy.ymin,
                "xmax": self.bounds_xy.xmax,
                "ymax": self.bounds_xy.ymax,
            },
            # Note: Full array export - consider RLE compression for production
            "w_occ": [w if w != float("inf") else None for w in self.w_occ],
            "occ_host": self.occ_host,
            "occ_link": self.occ_link,
            "occ_dwg": self.occ_dwg,
            "model_mask": self.model_mask,
            "model_edge_key": self.model_edge_key,
            "model_proxy_key": self.model_proxy_key,
            "model_proxy_mask": self.model_proxy_mask,
            "anno_key": self.anno_key,
            "anno_over_model": self.anno_over_model,
            "element_meta": self.element_meta,
            "anno_meta": self.anno_meta,
            "depth_test_stats": {
                "attempted": self.depth_test_attempted,
                "wins": self.depth_test_wins,
                "rejects": self.depth_test_rejects,
            },
        }


def _bresenham_line(i0, j0, i1, j1):
    """Generate cell coordinates along a line using Bresenham's algorithm.

    Args:
        i0, j0: Start cell coordinates
        i1, j1: End cell coordinates

    Yields:
        (i, j) cell coordinates along the line
    """
    di = abs(i1 - i0)
    dj = abs(j1 - j0)
    si = 1 if i0 < i1 else -1
    sj = 1 if j0 < j1 else -1
    err = di - dj

    i, j = i0, j0

    while True:
        yield (i, j)

        if i == i1 and j == j1:
            break

        e2 = 2 * err

        if e2 > -dj:
            err -= dj
            i += si

        if e2 < di:
            err += di
            j += sj
