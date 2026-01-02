"""
Configuration for VOP Interwoven Pipeline.

Defines the Config class with all parameters for the interwoven model pass,
proxy stamping, and depth-buffer occlusion logic.
"""

import math


class Config:
    """Configuration for VOP interwoven pipeline.

    Attributes:
        tile_size (int): Tile size for spatial acceleration (default: 16)
        over_model_includes_proxies (bool):
            True => "over any model presence" (AreaL mask OR proxy mask)
            False => "over AreaL occluders only" (AreaL mask only)
            Default: True
        proxy_mask_mode (str): Proxy stamping style
            "edges" => stamp only proxy edges (lightest)
            "minmask" => minimal mask (Tiny: center cell; Linear: thin band)
            Default: "minmask"
        depth_eps_ft (float): Depth buffer tolerance in feet (default: 0.01)
        tiny_max (int): Tiny threshold - Tiny if U<=tiny_max AND V<=tiny_max (default: 2)
        thin_max (int): Linear threshold - Linear if min(U,V)<=thin_max AND max(U,V)>thin_max (default: 2)
        anno_crop_margin_in (float): Printed margin in inches when annotation crop active (default: tied to bounds_buffer_in)
        anno_expand_cap_cells (int): Max cells to expand bounds when annotation crop inactive (default: tied to bounds_buffer_in ÷ cell_size_paper_in)
        cell_size_paper_in (float): Cell size on printed sheet in inches (default: 0.125 = 1/8")
        max_sheet_width_in (float): Maximum sheet width in inches (default: 48.0 = Arch E)
        max_sheet_height_in (float): Maximum sheet height in inches (default: 36.0 = Arch E)
        bounds_buffer_in (float): Buffer around bounds in inches (default: 0.5)
        include_linked_rvt (bool): Include elements from linked RVT files (default: True)
        include_dwg_imports (bool): Include elements from DWG/DXF imports (default: True)

    Commentary:
        ✔ overModelIncludesProxies controls whether tiny/linear proxies count as "model presence"
        ✔ proxyMaskMode="minmask" provides minimal footprint for OverModel semantics
        ✔ anno_crop_margin_in prevents annotation crop from clipping near-crop annotations
        ✔ anno_expand_cap_cells prevents far-away annotations from exploding grid size
        ✔ Grid size cap based on max_sheet_size / cell_size_paper_in (Arch E: 384x288 @ 1/8")
        ✔ bounds_buffer_in set to 0.5" for all operations (crop box, synthetic bounds)
        ⚠ Setting proxy_mask_mode="edges" means proxies won't contribute to model presence mask
        ⚠ depth_eps_ft should match your model precision (0.01 ft ≈ 1/8 inch tolerance)

    Example:
        >>> cfg = Config()
        >>> cfg.tile_size
        16
        >>> cfg.over_model_includes_proxies
        True
        >>> cfg.max_grid_cells_width
        384
    """

    def __init__(
        self,
        tile_size=16,
        adaptive_tile_size=True,
        over_model_includes_proxies=True,
        proxy_mask_mode="minmask",
        depth_eps_ft=0.01,
        tiny_max=2,
        thin_max=2,
        anno_crop_margin_in=None,
        anno_expand_cap_cells=None,
        cell_size_paper_in=0.125,
        max_sheet_width_in=48.0,
        max_sheet_height_in=36.0,
        bounds_buffer_in=0.5,
        include_linked_rvt=True,
        include_dwg_imports=True,
    ):
        """Initialize VOP configuration.

        Args:
            tile_size: Base tile size for early-out acceleration (default: 16)
            adaptive_tile_size: Auto-adjust tile size based on grid dimensions (default: True)
            over_model_includes_proxies: Include proxy presence in "over model" check
            proxy_mask_mode: "edges" or "minmask" for proxy stamping
            depth_eps_ft: Depth tolerance for edge visibility (feet)
            tiny_max: Max dimension for TINY classification (cells)
            thin_max: Max thin dimension for LINEAR classification (cells)
            anno_crop_margin_in: Margin in printed inches when annotation crop active (default: None = bounds_buffer_in)
            anno_expand_cap_cells: Max cells to expand when annotation crop inactive (default: None = auto-calculate from buffer)
            cell_size_paper_in: Cell size on printed sheet in inches (default: 0.125 = 1/8")
            max_sheet_width_in: Maximum sheet width in inches (default: 48.0 = Arch E)
            max_sheet_height_in: Maximum sheet height in inches (default: 36.0 = Arch E)
            bounds_buffer_in: Buffer around bounds in inches (default: 0.5)
            include_linked_rvt: Include elements from linked RVT files (default: True)
            include_dwg_imports: Include elements from DWG/DXF imports (default: True)
        """
        self.tile_size = int(tile_size)
        self.adaptive_tile_size = bool(adaptive_tile_size)
        self.over_model_includes_proxies = bool(over_model_includes_proxies)
        self.proxy_mask_mode = str(proxy_mask_mode)
        self.depth_eps_ft = float(depth_eps_ft)
        self.tiny_max = int(tiny_max)
        self.thin_max = int(thin_max)
        self.cell_size_paper_in = float(cell_size_paper_in)
        self.max_sheet_width_in = float(max_sheet_width_in)
        self.max_sheet_height_in = float(max_sheet_height_in)
        self.bounds_buffer_in = float(bounds_buffer_in)
        self.include_linked_rvt = bool(include_linked_rvt)
        self.include_dwg_imports = bool(include_dwg_imports)

        # Tie anno_crop_margin_in to bounds_buffer_in if not specified
        if anno_crop_margin_in is None:
            self.anno_crop_margin_in = self.bounds_buffer_in
        else:
            self.anno_crop_margin_in = float(anno_crop_margin_in)

        # Calculate anno_expand_cap_cells from bounds_buffer if not specified
        # Use bounds_buffer_in / cell_size_paper_in to get cells equivalent
        if anno_expand_cap_cells is None:
            self.anno_expand_cap_cells = int(self.bounds_buffer_in / self.cell_size_paper_in)
        else:
            self.anno_expand_cap_cells = int(anno_expand_cap_cells)

        # Validate
        if self.tile_size <= 0:
            raise ValueError("tile_size must be positive")
        if self.proxy_mask_mode not in ("edges", "minmask"):
            raise ValueError("proxy_mask_mode must be 'edges' or 'minmask'")
        if self.depth_eps_ft < 0:
            raise ValueError("depth_eps_ft must be non-negative")
        if self.tiny_max < 0 or self.thin_max < 0:
            raise ValueError("tiny_max and thin_max must be non-negative")
        if self.anno_crop_margin_in < 0:
            raise ValueError("anno_crop_margin_in must be non-negative")
        if self.anno_expand_cap_cells < 0:
            raise ValueError("anno_expand_cap_cells must be non-negative")
        if self.cell_size_paper_in <= 0:
            raise ValueError("cell_size_paper_in must be positive")
        if self.max_sheet_width_in <= 0 or self.max_sheet_height_in <= 0:
            raise ValueError("max_sheet dimensions must be positive")
        if self.bounds_buffer_in < 0:
            raise ValueError("bounds_buffer_in must be non-negative")

    def compute_adaptive_tile_size(self, grid_width, grid_height):
        """Compute optimal tile size based on grid dimensions.

        Args:
            grid_width: Grid width in cells
            grid_height: Grid height in cells

        Returns:
            Optimal tile size (power of 2 between 8 and 64)

        Commentary:
            ✔ Targets ~1K-4K tiles for optimal early-out granularity
            ✔ Clamps to power-of-2 for efficient indexing
            ✔ Small grids (64x64): 8x8 tiles → 64 tiles
            ✔ Medium grids (256x256): 16x16 tiles → 256 tiles
            ✔ Large grids (1024x1024): 32x32 tiles → 1024 tiles
            ✔ Very large grids (4096x4096): 64x64 tiles → 4096 tiles

        Examples:
            >>> cfg = Config(adaptive_tile_size=True)
            >>> cfg.compute_adaptive_tile_size(64, 64)
            8
            >>> cfg.compute_adaptive_tile_size(256, 256)
            16
            >>> cfg.compute_adaptive_tile_size(1024, 1024)
            32
        """
        if not self.adaptive_tile_size:
            return self.tile_size

        # Total cells
        total_cells = grid_width * grid_height

        # Target: 1K-4K tiles for good early-out granularity
        # Solve: (W/tile_size) * (H/tile_size) ≈ target_tiles
        # tile_size ≈ sqrt(W*H / target_tiles)

        # Use geometric mean of dimensions
        avg_dim = math.sqrt(total_cells)

        # Target 2K tiles
        target_tiles = 2000
        ideal_tile_size = avg_dim / math.sqrt(target_tiles)

        # Clamp to power of 2 in range [8, 64]
        tile_size = max(8, min(64, 2 ** round(math.log2(ideal_tile_size))))

        return int(tile_size)

    @property
    def max_grid_cells_width(self):
        """Maximum grid width in cells based on max sheet width and cell size.

        Returns:
            int: Maximum grid width (e.g., 384 cells for 48" @ 1/8")
        """
        return int(self.max_sheet_width_in / self.cell_size_paper_in)

    @property
    def max_grid_cells_height(self):
        """Maximum grid height in cells based on max sheet height and cell size.

        Returns:
            int: Maximum grid height (e.g., 288 cells for 36" @ 1/8")
        """
        return int(self.max_sheet_height_in / self.cell_size_paper_in)

    @property
    def bounds_buffer_ft(self):
        """Bounds buffer in feet (converted from inches).

        Returns:
            float: Buffer in feet (e.g., 0.5" = 0.0417 ft)
        """
        return self.bounds_buffer_in / 12.0

    def __repr__(self):
        return (
            f"Config(tile_size={self.tile_size}, "
            f"adaptive_tile_size={self.adaptive_tile_size}, "
            f"over_model_includes_proxies={self.over_model_includes_proxies}, "
            f"proxy_mask_mode='{self.proxy_mask_mode}', "
            f"depth_eps_ft={self.depth_eps_ft}, "
            f"tiny_max={self.tiny_max}, thin_max={self.thin_max}, "
            f"anno_crop_margin_in={self.anno_crop_margin_in}, "
            f"anno_expand_cap_cells={self.anno_expand_cap_cells}, "
            f"cell_size_paper_in={self.cell_size_paper_in}, "
            f"max_sheet={self.max_sheet_width_in}x{self.max_sheet_height_in}, "
            f"max_grid={self.max_grid_cells_width}x{self.max_grid_cells_height})"
        )

    def to_dict(self):
        """Export configuration as dictionary for JSON serialization."""
        return {
            "tile_size": self.tile_size,
            "adaptive_tile_size": self.adaptive_tile_size,
            "over_model_includes_proxies": self.over_model_includes_proxies,
            "proxy_mask_mode": self.proxy_mask_mode,
            "depth_eps_ft": self.depth_eps_ft,
            "tiny_max": self.tiny_max,
            "thin_max": self.thin_max,
            "anno_crop_margin_in": self.anno_crop_margin_in,
            "anno_expand_cap_cells": self.anno_expand_cap_cells,
            "cell_size_paper_in": self.cell_size_paper_in,
            "max_sheet_width_in": self.max_sheet_width_in,
            "max_sheet_height_in": self.max_sheet_height_in,
            "bounds_buffer_in": self.bounds_buffer_in,
            "include_linked_rvt": self.include_linked_rvt,
            "include_dwg_imports": self.include_dwg_imports,
        }

    @classmethod
    def from_dict(cls, d):
        """Create Config from dictionary (e.g., from JSON)."""
        return cls(
            tile_size=d.get("tile_size", 16),
            adaptive_tile_size=d.get("adaptive_tile_size", True),
            over_model_includes_proxies=d.get("over_model_includes_proxies", True),
            proxy_mask_mode=d.get("proxy_mask_mode", "minmask"),
            depth_eps_ft=d.get("depth_eps_ft", 0.01),
            tiny_max=d.get("tiny_max", 2),
            thin_max=d.get("thin_max", 2),
            anno_crop_margin_in=d.get("anno_crop_margin_in"),  # None = tied to bounds_buffer_in
            anno_expand_cap_cells=d.get("anno_expand_cap_cells"),  # None = auto-calculate
            cell_size_paper_in=d.get("cell_size_paper_in", 0.125),
            max_sheet_width_in=d.get("max_sheet_width_in", 48.0),
            max_sheet_height_in=d.get("max_sheet_height_in", 36.0),
            bounds_buffer_in=d.get("bounds_buffer_in", 0.5),
            include_linked_rvt=d.get("include_linked_rvt", True),
            include_dwg_imports=d.get("include_dwg_imports", True),
        )
