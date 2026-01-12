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
        self.w_min_tile = [float("inf")] * num_tiles
        self.w_max_tile = [float("-inf")] * num_tiles  # Minimum W-depth in tile (view-space depth)

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
            if depth > self.w_max_tile[tile_idx]:
                self.w_max_tile[tile_idx] = depth


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

            # Degenerate-at-grid fallback:
            # If the polyline collapses to <2 distinct cells at this resolution,
            # still stamp a single visible edge cell so symbolic/model linework
            # doesn't disappear entirely.
            if len(pts_ij) == 1:
                ii, jj = pts_ij[0]
                idx = self.get_cell_index(ii, jj)
                if idx is not None:
                    w_here = self.w_occ[idx]
                    if w_here == float("inf") or depth <= w_here:
                        is_text_bbox = (pl.get("strategy") == "cad_text_bbox")
                        if is_text_bbox:
                            self.stamp_proxy_edge_idx(idx, key_index, depth=depth)
                            self.model_proxy_mask[idx] = True
                            self.stamp_model_edge_idx(idx, key_index, depth=depth)
                        else:
                            self.stamp_model_edge_idx(idx, key_index, depth=depth)
                            if depth < w_here:
                                self.try_write_cell(ii, jj, w_depth=depth, source=source)
                filled += 1
                continue

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
                        is_text_bbox = (pl.get("strategy") == "cad_text_bbox")

                        if is_text_bbox:
                            # Make visible even if proxy edges are not rendered downstream:
                            # - stamp proxy channel
                            # - also stamp model edge channel
                            # - DO NOT contribute occlusion (non-occluding ink)
                            self.stamp_proxy_edge_idx(idx, key_index, depth=depth)
                            self.model_proxy_mask[idx] = True
                            self.stamp_model_edge_idx(idx, key_index, depth=depth)
                        else:
                            self.stamp_model_edge_idx(idx, key_index, depth=depth)

                            # Contribute to occlusion along the curve using try_write_cell
                            # This ensures DWG curves participate in depth testing
                            if depth < w_here:
                                self.try_write_cell(ii, jj, w_depth=depth, source=source)
                    filled += 1

        return filled
    
    def __init__(self, width, height, cell_size, bounds, tile_size=16, cfg=None):
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
        self.cfg = cfg

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
    # --- PR4: explicit "model present" semantics (unify across modules) ---

    @property
    def model_occ_mask(self):
        """Depth-tested interior occupancy ('truth'). Backed by legacy model_mask."""
        return self.model_mask

    @model_occ_mask.setter
    def model_occ_mask(self, value):
        self.model_mask = value

    @property
    def model_proxy_presence(self):
        """Heuristic presence mask (Tiny/Linear proxies). Backed by legacy model_proxy_mask."""
        return self.model_proxy_mask

    @model_proxy_presence.setter
    def model_proxy_presence(self, value):
        self.model_proxy_mask = value

    def has_model_occ(self, idx):
        """True if depth-tested interior occupancy is present at idx."""
        return (0 <= idx < len(self.model_mask)) and bool(self.model_mask[idx])

    def has_model_edge(self, idx):
        """True if a visible model edge label is present at idx."""
        return (0 <= idx < len(self.model_edge_key)) and (self.model_edge_key[idx] != -1)

    def has_model_proxy(self, idx):
        """True if proxy presence is present at idx."""
        return (0 <= idx < len(self.model_proxy_mask)) and bool(self.model_proxy_mask[idx])

    def has_model_present(self, idx, mode="occ", include_proxy_if_any=True):
        """
        Unified "model present" predicate with explicit mode.

        mode:
          - "occ"   : depth-tested interior (default)
          - "edge"  : boundary/edge labeling
          - "proxy" : heuristic proxy presence only
          - "any"   : occ OR edge (optionally OR proxy)

        include_proxy_if_any:
          - only applies when mode=="any"
          - default True because "any" should mean any model signal (occ/edge/proxy)

        """
        mode = (mode or "occ").lower()

        if mode == "occ":
            return self.has_model_occ(idx)
        if mode == "edge":
            return self.has_model_edge(idx)
        if mode == "proxy":
            return self.has_model_proxy(idx)
        if mode == "any":
            present = self.has_model_occ(idx) or self.has_model_edge(idx)
            if include_proxy_if_any:
                present = present or self.has_model_proxy(idx)
            return present

        raise ValueError("Unknown model-present mode: {0}".format(mode))

    def try_write_cell(self, i, j, w_depth, source, key_index=None):
        """Centralized cell write with depth testing (MANDATORY contract).

        Args:
            i, j: Cell indices (column, row)
            w_depth: W-depth from view-space UVW transform (depth = dot(p - O, forward))
            source: Source identifier ("HOST", "LINK", or "DWG")
            key_index: Optional element metadata index for attribution (PR8)

        Returns:
            True if depth won and cell was updated, False otherwise
        """
        idx = self.get_cell_index(i, j)
        if idx is None:
            return False

        self.depth_test_attempted += 1

        if w_depth < self.w_occ[idx]:
            was_empty = self.w_occ[idx] == float("inf")

            self.w_occ[idx] = w_depth
            self.model_mask[idx] = True

            # Mark exactly one occupancy layer based on source
            self.occ_host[idx] = False
            self.occ_link[idx] = False
            self.occ_dwg[idx] = False
            if source == "HOST":
                self.occ_host[idx] = True
            elif source == "LINK":
                self.occ_link[idx] = True
            elif source == "DWG":
                self.occ_dwg[idx] = True

            self.tile.update_w_min(i, j, w_depth)
            if was_empty:
                self.tile.update_filled_count(i, j, increment=1)

            # PR8 attribution (best-effort; never throws)
            if key_index is not None:
                try:
                    if 0 <= key_index < len(self.element_meta):
                        self.element_meta[key_index]["occlusion_cells"] += 1
                except Exception:
                    pass

            self.depth_test_wins += 1
            return True

        self.depth_test_rejects += 1
        return False

    def get_or_create_element_meta_index(self, elem_id, category, source_id, source_type="HOST", source_label=None):
        """Get or create metadata index for element.

        Args:
            elem_id: Revit element ID (integer)
            category: Element category name (string)
            source_id: Stable unique source identifier for indexing (e.g., "HOST", "RVT_LINK:...", "DWG_IMPORT:...")
            source_type: One of {"HOST", "LINK", "DWG"} (used for downstream layers)
            source_label: Optional friendly label for display (defaults to source_id)

        Returns:
            Integer index for this element's metadata

        Commentary:
            source_id: Used as unique key for indexing (must be unique per element per source)
            source_label: Used for display/logging (can be friendly, non-unique)
        """
        key = (elem_id, source_id)
        if key in self.element_meta_index_by_key:
            return self.element_meta_index_by_key[key]

        idx = len(self.element_meta)
        self.element_meta_index_by_key[key] = idx
        self.element_meta.append(
            {
                "elem_id": elem_id,
                "category": category,
                "source_id": source_id,
                "source_type": source_type,
                "source_label": source_label if source_label is not None else source_id,
                # PR8 counters (view-local; used for dominance + summaries)
                "occlusion_cells": 0,      # depth wins via try_write_cell
                "model_edge_cells": 0,     # visible edges stamped into model_edge_key
                "proxy_edge_cells": 0,     # proxy perimeter edges stamped into model_proxy_key
                # PR9 bbox provenance (best-effort, set by pipeline after wrapper resolve)
                "bbox_source": None,   # "view" | "model" | "none"
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

        # DIAG: model vs anno occupancy counts (helps catch "frame rectangles")
        try:
            n_model_occ = sum(1 for b in self.model_mask if bool(b))
            n_model_edge = sum(1 for k in self.model_edge_key if k != -1)
            n_model_proxy = sum(1 for k in self.model_proxy_key if k != -1)
            n_anno = sum(1 for k in self.anno_key if k != -1)
            n_overlap = sum(1 for b in self.anno_over_model if b)
            print("[diag][raster] model_occ={0} model_edge={1} model_proxy={2} anno={3} overlap={4} W={5} H={6}".format(
                n_model_occ, n_model_edge, n_model_proxy, n_anno, n_overlap, self.W, self.H
            ))
        except Exception:
            pass

    def stamp_model_edge_idx(self, idx, key_index, depth=0.0):
        """Stamp a model ink edge cell (edge-only occupancy), with depth visibility check."""
        if idx is None or not (0 <= idx < len(self.model_edge_key)):
            return False

        w_here = self.w_occ[idx]
        if w_here == float("inf") or depth <= w_here:
            if self.model_edge_key[idx] != key_index:
                self.model_edge_key[idx] = key_index
                try:
                    if 0 <= key_index < len(self.element_meta):
                        self.element_meta[key_index]["model_edge_cells"] += 1
                except Exception:
                    pass
            return True
        return False

    def stamp_proxy_edge_idx(self, idx, key_index, depth=0.0):
        """Stamp a proxy perimeter edge cell into proxy channel (config-gated in pipeline)."""
        if idx is None or not (0 <= idx < len(self.model_proxy_key)):
            return False

        w_here = self.w_occ[idx]
        if w_here == float("inf") or depth <= w_here:
            if self.model_proxy_key[idx] != key_index:
                self.model_proxy_key[idx] = key_index
                try:
                    if 0 <= key_index < len(self.element_meta):
                        self.element_meta[key_index]["proxy_edge_cells"] += 1
                except Exception:
                    pass
            return True
        return False

    def rasterize_proxy_loops(self, loops, key_index, depth=0.0, source="HOST", write_proxy_edges=False):
        """Rasterize proxy footprint loops: occlusion fill ALWAYS; proxy edges optionally.

        Critical PR8 rule:
          - never stamps model_edge_key
          - never produces model ink by default
        """
        if not loops:
            return 0

        filled_count = 0

        for loop in loops:
            points_uv = loop.get("points", [])
            is_hole = loop.get("is_hole", False)

            if len(points_uv) < 3:
                continue

            # Clip in UV first to avoid clamp-to-grid distortion at raster bounds.
            xmin = self.bounds_xy.xmin
            ymin = self.bounds_xy.ymin
            xmax = self.bounds_xy.xmax
            ymax = self.bounds_xy.ymax

            clipped_uv = _clip_poly_to_rect_uv([(p[0], p[1]) for p in points_uv], xmin, ymin, xmax, ymax)
            if len(clipped_uv) < 3:
                continue

            points_ij = []
            for (u, v) in clipped_uv:
                i = int((u - xmin) / self.cell_size_ft)
                j = int((v - ymin) / self.cell_size_ft)
                # Range-check (no clamping). Skip vertices that land out of bounds.
                if i < 0 or i >= self.W or j < 0 or j >= self.H:
                    continue
                points_ij.append((i, j))

            if len(points_ij) < 3:
                continue

            # 1) Occlusion interior fill (allowed for proxies)
            if not is_hole:
                filled_count += self._scanline_fill(points_ij, key_index, depth, source)

            # 2) Optional proxy perimeter edges (never model ink)
            if write_proxy_edges:
                for k in range(len(points_ij) - 1):
                    i0, j0 = points_ij[k]
                    i1, j1 = points_ij[k + 1]
                    for i, j in _bresenham_line(i0, j0, i1, j1):
                        idx = self.get_cell_index(i, j)
                        if idx is None:
                            continue
                        self.stamp_proxy_edge_idx(idx, key_index, depth=depth)

        return filled_count

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
                    self.stamp_model_edge_idx(idx, key_index, depth=depth)


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

    def dump_occlusion_debug(self, output_path_prefix, view_name="view"):
        """Dump w_occ and occupancy layers for debugging.

        Args:
            output_path_prefix: Path prefix for output files (e.g., "/tmp/debug")
            view_name: View name to include in filenames

        Outputs:
            {prefix}_{view}_w_occ.csv - W-depth buffer (u, v, w_depth)
            {prefix}_{view}_occ_host.csv - Host occupancy mask
            {prefix}_{view}_occ_link.csv - Link occupancy mask
            {prefix}_{view}_occ_dwg.csv - DWG occupancy mask

        Commentary:
            This is for visual debugging and regression checking.
            CSV format: one row per cell with u,v,value
        """
        import os
        import math

        # Sanitize view name for filename
        safe_view_name = view_name.replace(" ", "_").replace("/", "_").replace("\\", "_")

        # Dump w_occ (W-depth buffer)
        w_occ_path = "{0}_{1}_w_occ.csv".format(output_path_prefix, safe_view_name)
        with open(w_occ_path, 'w') as f:
            f.write("u,v,w_depth\n")
            for j in range(self.H):
                for i in range(self.W):
                    idx = j * self.W + i
                    w = self.w_occ[idx]
                    if w != float("inf"):
                        f.write("{0},{1},{2:.6f}\n".format(i, j, w))

        # Dump occ_host
        occ_host_path = "{0}_{1}_occ_host.csv".format(output_path_prefix, safe_view_name)
        with open(occ_host_path, 'w') as f:
            f.write("u,v,occupied\n")
            for j in range(self.H):
                for i in range(self.W):
                    idx = j * self.W + i
                    if self.occ_host[idx]:
                        f.write("{0},{1},1\n".format(i, j))

        # Dump occ_link
        occ_link_path = "{0}_{1}_occ_link.csv".format(output_path_prefix, safe_view_name)
        with open(occ_link_path, 'w') as f:
            f.write("u,v,occupied\n")
            for j in range(self.H):
                for i in range(self.W):
                    idx = j * self.W + i
                    if self.occ_link[idx]:
                        f.write("{0},{1},1\n".format(i, j))

        # Dump occ_dwg
        occ_dwg_path = "{0}_{1}_occ_dwg.csv".format(output_path_prefix, safe_view_name)
        with open(occ_dwg_path, 'w') as f:
            f.write("u,v,occupied\n")
            for j in range(self.H):
                for i in range(self.W):
                    idx = j * self.W + i
                    if self.occ_dwg[idx]:
                        f.write("{0},{1},1\n".format(i, j))

        # Optional: dump w_occ as grayscale image (PGM)
        if getattr(self.cfg, "debug_dump_occlusion_image", False):
            finite_depths = [d for d in self.w_occ if math.isfinite(d)]
            if finite_depths:
                d_min = min(finite_depths)
                d_max = max(finite_depths)
            else:
                d_min = 0.0
                d_max = 1.0

            scale = (d_max - d_min) if d_max > d_min else 1.0

            prefix = "{0}_{1}".format(output_path_prefix, safe_view_name)
            pgm_path = prefix + "_w_occ.pgm"

            with open(pgm_path, "w") as f:
                f.write("P2\n")
                f.write(f"{self.W} {self.H}\n")
                f.write("255\n")

                for j in range(self.H):
                    row = []
                    for i in range(self.W):
                        idx = self.get_cell_index(i, j)
                        d = self.w_occ[idx]
                        if not math.isfinite(d):
                            g = 255
                        else:
                            t = (d - d_min) / scale
                            g = int(max(0, min(255, 255 * t)))
                        row.append(str(g))
                    f.write(" ".join(row) + "\n")

        print("[DEBUG] Dumped occlusion layers:")
        print("  - {0}".format(w_occ_path))
        print("  - {0}".format(occ_host_path))
        print("  - {0}".format(occ_link_path))
        print("  - {0}".format(occ_dwg_path))
        if getattr(self.cfg, "debug_dump_occlusion_image", False):
            print("  - {0}".format(pgm_path))

    def to_dict(self):
        """Full raster payload for downstream exporters (PNG/CSV/metrics).

        IMPORTANT:
          - This must remain FULL. PNG + CSV rely on these arrays being present.
          - Debug JSON size trimming must happen at JSON export time, NOT here.
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
            # Bounds/resolution metadata (small, required for CSV contract)
            "bounds_meta": getattr(self, "bounds_meta", None),

            # Large per-cell arrays (required for PNG/CSV correctness)
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
            # Meta (can be large-ish, but not per-cell dense)
            "element_meta": self.element_meta,
            "anno_meta": self.anno_meta,
            # Stats
            "depth_test_attempted": self.depth_test_attempted,
            "depth_test_wins": self.depth_test_wins,
            "depth_test_rejects": self.depth_test_rejects,
        }


    @classmethod
    def from_dict(cls, d, cfg=None):
        """Reconstruct a ViewRaster from a dict payload (inverse of to_dict()).

        Intended for metrics/export recomputation paths (e.g., root cache extraction).
        """
        if d is None:
            raise ValueError("ViewRaster.from_dict: input is None")

        from .math_utils import Bounds2D

        W = int(d.get("width", 0))
        H = int(d.get("height", 0))
        cell_size_ft = float(d.get("cell_size_ft", 0.0))

        b = d.get("bounds_xy") or {}
        bounds = Bounds2D(
            b.get("xmin", 0.0),
            b.get("ymin", 0.0),
            b.get("xmax", 0.0),
            b.get("ymax", 0.0),
        )

        r = cls(W, H, cell_size_ft, bounds, cfg=cfg)

        # Optional bounds/resolution metadata (may be absent in older payloads)
        bm = d.get("bounds_meta", None)
        if bm is not None:
            try:
                r.bounds_meta = bm
            except Exception:
                pass

        # w_occ uses None as sentinel for +inf in JSON
        w_occ_in = d.get("w_occ") or []
        if w_occ_in:
            r.w_occ = [float("inf") if (w is None) else float(w) for w in w_occ_in]

        # Dense layers
        for k in ("occ_host", "occ_link", "occ_dwg",
                  "model_mask", "model_edge_key", "model_proxy_key",
                  "model_proxy_mask", "anno_key", "anno_over_model"):
            v = d.get(k)
            if v is not None:
                setattr(r, k, v)

        # Meta lists
        r.element_meta = d.get("element_meta") or []
        r.anno_meta = d.get("anno_meta") or []

        # Stats
        r.depth_test_attempted = int(d.get("depth_test_attempted", 0))
        r.depth_test_wins = int(d.get("depth_test_wins", 0))
        r.depth_test_rejects = int(d.get("depth_test_rejects", 0))

        return r

    def to_debug_dict(self, detail="summary"):
        """Smaller debug payload for JSON export only.

        detail:
          - "summary": structural info only
          - "medium": summary + lightweight stats + meta lists (no per-cell arrays)
          - "full": same as to_dict()
        """
        d = (detail or "summary").strip().lower()
        if d not in ("summary", "medium", "full"):
            d = "summary"

        if d == "full":
            out = self.to_dict()
            out["debug_detail"] = "full"
            return out

        out = {
            "width": self.W,
            "height": self.H,
            "cell_size_ft": self.cell_size_ft,
            "bounds_xy": {
                "xmin": self.bounds_xy.xmin,
                "ymin": self.bounds_xy.ymin,
                "xmax": self.bounds_xy.xmax,
                "ymax": self.bounds_xy.ymax,
            },
            "debug_detail": d,
        }

        if d == "medium":
            # Keep meta + depth stats, but skip per-cell arrays
            out["element_meta"] = self.element_meta
            out["anno_meta"] = self.anno_meta
            out["depth_test_stats"] = {
                "attempted": self.depth_test_attempted,
                "wins": self.depth_test_wins,
                "rejects": self.depth_test_rejects,
            }

            # Small derived counts (best-effort)
            try:
                n = int(self.W) * int(self.H)
                out["counts"] = {
                    "total_cells": n,
                    "occ_cells": sum(1 for w in self.w_occ if w != float("inf")),
                    "model_cells": sum(1 for b in self.model_mask if b),
                    "anno_cells": sum(1 for k in self.anno_key if k != -1),
                    "overlap_cells": sum(1 for b in self.anno_over_model if b),
                }
            except Exception:
                out["counts"] = None

        return out

def _clip_poly_to_rect_uv(points_uv, xmin, ymin, xmax, ymax):
    """Clip a polygon (list[(u,v)]) to an axis-aligned rect in UV using Sutherland–Hodgman.
    Returns list[(u,v)] (may be empty). Never raises.
    """
    if not points_uv or len(points_uv) < 3:
        return []

    def inside(p, edge):
        u, v = p
        if edge == "left":
            return u >= xmin
        if edge == "right":
            return u <= xmax
        if edge == "bottom":
            return v >= ymin
        if edge == "top":
            return v <= ymax
        return True

    def intersect(p1, p2, edge):
        u1, v1 = p1
        u2, v2 = p2
        du = u2 - u1
        dv = v2 - v1

        # Parallel to boundary → return p2 (best effort; caller may drop degenerates)
        if edge in ("left", "right"):
            x = xmin if edge == "left" else xmax
            if du == 0:
                return (x, v2)
            t = (x - u1) / du
            return (x, v1 + t * dv)

        y = ymin if edge == "bottom" else ymax
        if dv == 0:
            return (u2, y)
        t = (y - v1) / dv
        return (u1 + t * du, y)

    def clip_against_edge(poly, edge):
        if not poly:
            return []
        out = []
        prev = poly[-1]
        prev_in = inside(prev, edge)
        for curr in poly:
            curr_in = inside(curr, edge)
            if curr_in:
                if not prev_in:
                    out.append(intersect(prev, curr, edge))
                out.append(curr)
            else:
                if prev_in:
                    out.append(intersect(prev, curr, edge))
            prev, prev_in = curr, curr_in
        return out

    poly = points_uv
    for edge in ("left", "right", "bottom", "top"):
        poly = clip_against_edge(poly, edge)
        if len(poly) < 3:
            return []
    return poly

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
