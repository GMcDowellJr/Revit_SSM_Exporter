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
        linear_band_thickness_cells (float): Band width for detail/drafting lines in cells (default: 1.0)

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
        anno_expand_cap_in=None,
        cell_size_paper_in=0.125,
        max_sheet_width_in=48.0,
        max_sheet_height_in=36.0,
        bounds_buffer_in=0.0,
        include_linked_rvt=True,
        include_dwg_imports=True,
        # Detail line rendering (archive parity)
        linear_band_thickness_cells=1.0,
        # Debug and diagnostics
        debug_dump_occlusion=False,
        debug_dump_path=r"C:\temp\vop_output",
        # Dump w_occ as grayscale image (PGM)
        debug_dump_occlusion_image = True,
    
        # Debug dump prefix (optional override)
        debug_dump_prefix=None,
        # Tier-B adaptive configuration
        tierb_cell_size_ref_ft=1.0,
        tierb_area_fraction=0.005,
        tierb_margin_cells_min=1,
        tierb_margin_cells_max=4,
        tierb_area_thresh_min=50,
        tierb_area_thresh_max=2000,

        # PR11: Collector consolidation / broad-phase performance knobs
        enable_multicategory_filter=True,
        coarse_spatial_filter_enabled=False,
        coarse_spatial_filter_pad_ft=0.0,

        # PR11: Synthetic extents scan budgets (safety + auditable fallbacks)
        extents_scan_max_elements=50000,
        extents_scan_time_budget_s=0.50,
        
        # PR12: Geometry caching (bounded LRU)
        geometry_cache_max_items=2048,
        
        # Perf: per-view timings (coarse always; optional sub-step)
        perf_collect_timings=True,
        perf_subtimings=True,

        # ────────────────────────────────────────────────────────────────────
        # Persistent view-level cache (disk-backed)
        #
        # If enabled and view_cache_dir is set, the pipeline will attempt to
        # skip entire views when the view "signature" matches the cached one.
        #
        # NOTE: Signature is conservative but not omniscient; it primarily
        # tracks view state + exporter config. If doc is dirty (unsaved),
        # skipping is disabled by default.
        view_cache_enabled=True,
        view_cache_dir=None,  # e.g. r"C:\temp\vop_output\.vop_view_cache"
        view_cache_require_doc_unmodified=True,

        # Phase 2: Element cache for bbox reuse across views
        use_element_cache=True,
        element_cache_max_items=10000,
        signature_bbox_precision=2,

        # Phase 2.5: Persistent element cache for cross-run reuse
        element_cache_persist=True,  # Save/load cache between runs
        element_cache_export_csv=True,  # Export analysis CSV
        element_cache_detect_changes=True,  # Compare with previous run
        element_cache_change_tolerance=0.01,  # Position/size tolerance (feet)

        # Strategy diagnostics: track geometry extraction performance
        export_strategy_diagnostics=False,  # Export strategy diagnostics CSV and print summary

        # Memory management: control raster retention behavior
        # True = keep full rasters in memory (needed for streaming exports)
        # False = discard rasters after cache writes (memory efficient)
        retain_rasters_in_memory=False,  # Default True for backward compatibility
        
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
            linear_band_thickness_cells: Band width for detail lines in cells (default: 1.0)
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

        # Detail line rendering (archive parity)
        # Archive reference: archive/refactor1 used "linear_band_thickness_cells"
        # Width of oriented bands for detail/drafting lines, in cell units.
        # Default: 1.0 creates 2-cell-wide bands (±0.5 cells from centerline)
        self.linear_band_thickness_cells = float(linear_band_thickness_cells)

        # PR11: collector knobs
        self.enable_multicategory_filter = bool(enable_multicategory_filter)
        self.coarse_spatial_filter_enabled = bool(coarse_spatial_filter_enabled)
        self.coarse_spatial_filter_pad_ft = float(coarse_spatial_filter_pad_ft)

        # PR11: bounds scan budgets
        self.extents_scan_max_elements = int(extents_scan_max_elements) if extents_scan_max_elements is not None else None
        self.extents_scan_time_budget_s = float(extents_scan_time_budget_s) if extents_scan_time_budget_s is not None else None

        # PR12: geometry cache
        self.geometry_cache_max_items = int(geometry_cache_max_items) if geometry_cache_max_items is not None else 0

        # Perf: timings
        self.perf_collect_timings = bool(perf_collect_timings)
        self.perf_subtimings = bool(perf_subtimings)

        # Debug and diagnostics
        self.debug_dump_occlusion = bool(debug_dump_occlusion)
        self.debug_dump_path = debug_dump_path  # None = auto-generate from view name
        self.debug_dump_prefix = debug_dump_prefix
        self.debug_dump_occlusion_image = bool(debug_dump_occlusion_image)


        # Tier-B adaptive configuration
        self.tierb_cell_size_ref_ft = float(tierb_cell_size_ref_ft)
        self.tierb_area_fraction = float(tierb_area_fraction)
        self.tierb_margin_cells_min = int(tierb_margin_cells_min)
        self.tierb_margin_cells_max = int(tierb_margin_cells_max)
        self.tierb_area_thresh_min = int(tierb_area_thresh_min)
        self.tierb_area_thresh_max = int(tierb_area_thresh_max)

        # Tie anno_crop_margin_in to bounds_buffer_in if not specified
        if anno_crop_margin_in is None:
            self.anno_crop_margin_in = self.bounds_buffer_in
        else:
            self.anno_crop_margin_in = float(anno_crop_margin_in)

        # Explicit printed-inches cap (preferred over anno_expand_cap_cells when present)
        if anno_expand_cap_in is None:
            self.anno_expand_cap_in = None
        else:
            self.anno_expand_cap_in = float(anno_expand_cap_in)

        # Calculate anno_expand_cap_cells from bounds_buffer if not specified
        # Use bounds_buffer_in / cell_size_paper_in to get cells equivalent
        if anno_expand_cap_cells is None:
            self.anno_expand_cap_cells = int(self.bounds_buffer_in / self.cell_size_paper_in)
        else:
            self.anno_expand_cap_cells = int(anno_expand_cap_cells)

        # Validate
        if self.anno_expand_cap_in is not None and self.anno_expand_cap_in < 0:
            raise ValueError("anno_expand_cap_in must be non-negative or None")

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
        if self.linear_band_thickness_cells < 0:
            raise ValueError("linear_band_thickness_cells must be non-negative")

        # PR11 validation (explicit semantics; None means "no budget")
        if self.coarse_spatial_filter_pad_ft < 0:
            raise ValueError("coarse_spatial_filter_pad_ft must be non-negative")

        if self.extents_scan_max_elements is not None and self.extents_scan_max_elements <= 0:
            raise ValueError("extents_scan_max_elements must be positive or None")

        if self.extents_scan_time_budget_s is not None and self.extents_scan_time_budget_s <= 0:
            raise ValueError("extents_scan_time_budget_s must be positive or None")

        # PR12 validation: 0 disables caching (explicit).
        if self.geometry_cache_max_items < 0:
            raise ValueError("geometry_cache_max_items must be >= 0")

        # Persistent view-level cache
        self.view_cache_enabled = view_cache_enabled
        self.view_cache_dir = view_cache_dir
        self.view_cache_require_doc_unmodified = view_cache_require_doc_unmodified

        # Phase 2: Element cache for bbox reuse
        self.use_element_cache = bool(use_element_cache)
        self.element_cache_max_items = int(element_cache_max_items)
        self.signature_bbox_precision = int(signature_bbox_precision)

        # Phase 2.5: Persistent element cache
        self.element_cache_persist = bool(element_cache_persist)
        self.element_cache_export_csv = bool(element_cache_export_csv)
        self.element_cache_detect_changes = bool(element_cache_detect_changes)
        self.element_cache_change_tolerance = float(element_cache_change_tolerance)

        # Strategy diagnostics
        self.export_strategy_diagnostics = bool(export_strategy_diagnostics)

        # Memory management
        self.retain_rasters_in_memory = bool(retain_rasters_in_memory)
        
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

    @property
    def silhouette_tiny_thresh_ft(self):
        """Threshold for tiny elements in feet (default: 3.0 ft).

        Returns:
            float: Threshold in feet
        """
        return getattr(self, '_silhouette_tiny_thresh_ft', 3.0)

    @property
    def silhouette_large_thresh_ft(self):
        """Threshold for large elements in feet (default: 20.0 ft).

        Returns:
            float: Threshold in feet
        """
        return getattr(self, '_silhouette_large_thresh_ft', 20.0)

    @property
    def coarse_tess_max_verts(self):
        """Maximum vertices per face for coarse tessellation (default: 20).

        Returns:
            int: Maximum vertices
        """
        return getattr(self, '_coarse_tess_max_verts', 20)

    def get_silhouette_strategies(self, uv_mode):
        """Get silhouette extraction strategies for a given UV mode (shape).

        Args:
            uv_mode: 'TINY', 'LINEAR', or 'AREAL'

        Returns:
            List of strategy names to try in order

        Commentary:
            Shape-based strategy selection:
            - TINY: Small in both U and V → bbox (fast, sufficient)
            - LINEAR: Thin beams/columns/pipes → obb → bbox (captures rotation, no concavity)
            - AREAL: Large area elements → silhouette_edges → obb → bbox (preserves L-shapes)

            Key insight: LINEAR elements (beams, columns) are rarely concave,
            so OBB is perfect. AREAL elements (floors, walls) can be concave (L, U, C),
            so they need true silhouette extraction.
        """
        if uv_mode == 'TINY':
            # Small elements: bbox is good enough
            return ['bbox']
        elif uv_mode == 'LINEAR':
            # Thin/long elements (beams, columns, pipes): OBB captures rotation
            return ['obb', 'bbox']
        elif uv_mode == 'AREAL':
            # Large area elements:
            # Primary: planar front-facing face loops (semantic, preserves openings)
            # Fallback: silhouette edges (temporary), then OBB → BBox
            return ['planar_face_loops', 'silhouette_edges', 'obb', 'bbox']
        else:
            # Default fallback: full chain
            return ['silhouette_edges', 'obb', 'bbox']

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
            f"max_grid={self.max_grid_cells_width}x{self.max_grid_cells_height}"
            f"multicat={self.enable_multicategory_filter}, "
            f"coarse_spatial={self.coarse_spatial_filter_enabled}, "
            f"coarse_pad_ft={self.coarse_spatial_filter_pad_ft}, "
            f"extents_max={self.extents_scan_max_elements}, "
            f"extents_budget_s={self.extents_scan_time_budget_s}) "
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
            # Detail line rendering
            "linear_band_thickness_cells": self.linear_band_thickness_cells,
            # PR11 knobs
            "enable_multicategory_filter": self.enable_multicategory_filter,
            "coarse_spatial_filter_enabled": self.coarse_spatial_filter_enabled,
            "coarse_spatial_filter_pad_ft": self.coarse_spatial_filter_pad_ft,
            "extents_scan_max_elements": self.extents_scan_max_elements,
            "extents_scan_time_budget_s": self.extents_scan_time_budget_s,
            # PR12: geometry cache
            "geometry_cache_max_items": self.geometry_cache_max_items,
            
            "perf_collect_timings": self.perf_collect_timings,
            "perf_subtimings": self.perf_subtimings,
            
            "view_cache_enabled": self.view_cache_enabled,
            "view_cache_dir": self.view_cache_dir,
            "view_cache_require_doc_unmodified": self.view_cache_require_doc_unmodified,
            # Phase 2: Element cache
            "use_element_cache": self.use_element_cache,
            "element_cache_max_items": self.element_cache_max_items,
            "signature_bbox_precision": self.signature_bbox_precision,
            # Phase 2.5: Persistent element cache
            "element_cache_persist": self.element_cache_persist,
            "element_cache_export_csv": self.element_cache_export_csv,
            "element_cache_detect_changes": self.element_cache_detect_changes,
            "element_cache_change_tolerance": self.element_cache_change_tolerance,
            # Strategy diagnostics
            "export_strategy_diagnostics": self.export_strategy_diagnostics,
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
            # Detail line rendering
            linear_band_thickness_cells=d.get("linear_band_thickness_cells", 1.0),
            # PR11 knobs
            enable_multicategory_filter=d.get("enable_multicategory_filter", True),
            coarse_spatial_filter_enabled=d.get("coarse_spatial_filter_enabled", False),
            coarse_spatial_filter_pad_ft=d.get("coarse_spatial_filter_pad_ft", 0.0),
            extents_scan_max_elements=d.get("extents_scan_max_elements", 50000),
            extents_scan_time_budget_s=d.get("extents_scan_time_budget_s", 0.50),
            # PR12
            geometry_cache_max_items=d.get("geometry_cache_max_items", 2048),

            perf_collect_timings=d.get("perf_collect_timings", True),
            perf_subtimings=d.get("perf_subtimings", False),
                        
            view_cache_enabled=d.get("view_cache_enabled", True),
            view_cache_dir=d.get("view_cache_dir", None),
            view_cache_require_doc_unmodified=d.get("view_cache_require_doc_unmodified", True),

            # Phase 2: Element cache
            use_element_cache=d.get("use_element_cache", True),
            element_cache_max_items=d.get("element_cache_max_items", 10000),
            signature_bbox_precision=d.get("signature_bbox_precision", 2),

            # Phase 2.5: Persistent element cache
            element_cache_persist=d.get("element_cache_persist", True),
            element_cache_export_csv=d.get("element_cache_export_csv", True),
            element_cache_detect_changes=d.get("element_cache_detect_changes", True),
            element_cache_change_tolerance=d.get("element_cache_change_tolerance", 0.01),

            # Strategy diagnostics
            export_strategy_diagnostics=d.get("export_strategy_diagnostics", True),

        )
