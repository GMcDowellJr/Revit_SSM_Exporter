"""
Raster data structures for VOP interwoven pipeline.

Provides ViewRaster and TileMap classes for tracking occlusion state,
depth buffers, and edge/annotation layers per view.
"""


class TileMap:
    """Tile-based spatial acceleration structure for early-out occlusion testing.

    Attributes:
        tile_size: Size of each tile in cells (e.g., 16x16)
        tiles_x, tiles_y: Number of tiles in X and Y dimensions
        filled_count: List of filled cell counts per tile
        z_min_tile: List of minimum depth values per tile

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
        self.z_min_tile = [float("inf")] * num_tiles  # Minimum depth in tile

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

    def update_z_min(self, cell_i, cell_j, depth):
        """Update minimum depth for tile containing cell.

        Args:
            cell_i, cell_j: Cell indices
            depth: Depth value to compare
        """
        tile_idx = self.get_tile_index(cell_i, cell_j)
        if 0 <= tile_idx < len(self.z_min_tile):
            if depth < self.z_min_tile[tile_idx]:
                self.z_min_tile[tile_idx] = depth


class ViewRaster:
    """Raster representation of a single view for VOP interwoven pipeline.

    Stores all occlusion state, depth buffers, edge layers, and annotation
    data for one Revit view.

    Attributes:
        W, H: Raster dimensions in cells
        cell_size_ft: Cell size in model units (feet)
        bounds_xy: Bounds2D in view-local XY

        # AreaL truth occlusion from 3D model
        model_mask: Boolean array [W*H] - interior coverage
        z_min: Float array [W*H] - nearest depth (+inf if empty)
        tile: TileMap for early-out testing

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

    Example:
        >>> from .math_utils import Bounds2D
        >>> bounds = Bounds2D(0.0, 0.0, 100.0, 100.0)
        >>> raster = ViewRaster(width=64, height=64, cell_size=1.0, bounds=bounds, tile_size=16)
        >>> raster.W, raster.H
        (64, 64)
        >>> raster.set_cell_filled(10, 10, depth=5.0)
        >>> raster.z_min[10 * 64 + 10]
        5.0
    """

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

        # AreaL truth occlusion
        self.model_mask = [False] * N
        self.z_min = [float("inf")] * N

        # Tile acceleration
        self.tile = TileMap(tile_size, self.W, self.H)

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

    def set_cell_filled(self, i, j, depth=None):
        """Mark cell as filled with optional depth.

        Args:
            i, j: Cell indices
            depth: Optional depth value (updates z_min if provided and nearer)

        Returns:
            True if cell was updated, False if out of bounds
        """
        idx = self.get_cell_index(i, j)
        if idx is None:
            return False

        was_empty = not self.model_mask[idx]

        self.model_mask[idx] = True

        if depth is not None:
            if depth < self.z_min[idx]:
                self.z_min[idx] = depth
                self.tile.update_z_min(i, j, depth)

        if was_empty:
            self.tile.update_filled_count(i, j, increment=1)

        return True

    def get_or_create_element_meta_index(self, elem_id, category, source="HOST"):
        """Get or create metadata index for element.

        Args:
            elem_id: Revit element ID (integer)
            category: Element category name (string)
            source: Source type ("HOST", "RVT_LINK", etc.)

        Returns:
            Integer index for this element's metadata
        """
        key = (elem_id, source)
        if key in self.element_meta_index_by_key:
            return self.element_meta_index_by_key[key]

        idx = len(self.element_meta)
        self.element_meta_index_by_key[key] = idx
        self.element_meta.append(
            {"elem_id": elem_id, "category": category, "source": source}
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

    def rasterize_silhouette_loops(self, loops, key_index, depth=0.0):
        """Rasterize element silhouette loops into model layers.

        Args:
            loops: List of loop dicts from silhouette.get_element_silhouette()
                   Each loop has: {'points': [(u, v), ...], 'is_hole': bool}
            key_index: Element metadata index (for edge tracking)
            depth: Depth value for z-buffer (default: 0.0)

        Returns:
            Number of cells filled

        Commentary:
            - Converts loop points from view UV to cell indices
            - Rasterizes loop edges using Bresenham line algorithm
            - Fills interior using scanline fill
            - Updates model_mask, z_min, and model_edge_key
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
            for u, v in points_uv:
                i = int((u - self.bounds_xy.xmin) / self.cell_size_ft)
                j = int((v - self.bounds_xy.ymin) / self.cell_size_ft)

                # Clamp to bounds
                i = max(0, min(i, self.W - 1))
                j = max(0, min(j, self.H - 1))

                points_ij.append((i, j))

            # Rasterize edges
            for k in range(len(points_ij) - 1):
                i0, j0 = points_ij[k]
                i1, j1 = points_ij[k + 1]

                # Draw line using Bresenham
                for i, j in _bresenham_line(i0, j0, i1, j1):
                    idx = self.get_cell_index(i, j)
                    if idx is not None:
                        self.model_edge_key[idx] = key_index

            # Fill interior (if not a hole)
            if not is_hole:
                filled_count += self._scanline_fill(points_ij, key_index, depth)

        return filled_count

    def _scanline_fill(self, points_ij, key_index, depth):
        """Fill polygon interior using scanline algorithm.

        Args:
            points_ij: List of (i, j) cell coordinates forming closed polygon
            key_index: Element metadata index
            depth: Depth value for z-buffer

        Returns:
            Number of cells filled
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
                    if self.set_cell_filled(i, j, depth=depth):
                        idx = self.get_cell_index(i, j)
                        if idx is not None:
                            # Only set edge key if not already set (preserve edges from loop outline)
                            if self.model_edge_key[idx] == -1:
                                self.model_edge_key[idx] = key_index
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
            "model_mask": self.model_mask,
            "z_min": [z if z != float("inf") else None for z in self.z_min],
            "model_edge_key": self.model_edge_key,
            "model_proxy_key": self.model_proxy_key,
            "model_proxy_mask": self.model_proxy_mask,
            "anno_key": self.anno_key,
            "anno_over_model": self.anno_over_model,
            "element_meta": self.element_meta,
            "anno_meta": self.anno_meta,
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
