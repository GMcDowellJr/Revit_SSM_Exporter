"""
Configuration for VOP Interwoven Pipeline.

Defines the Config class with all parameters for the interwoven model pass,
proxy stamping, and depth-buffer occlusion logic.
"""


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

    Commentary:
        ✔ overModelIncludesProxies controls whether tiny/linear proxies count as "model presence"
        ✔ proxyMaskMode="minmask" provides minimal footprint for OverModel semantics
        ⚠ Setting proxy_mask_mode="edges" means proxies won't contribute to model presence mask
        ⚠ depth_eps_ft should match your model precision (0.01 ft ≈ 1/8 inch tolerance)

    Example:
        >>> cfg = Config()
        >>> cfg.tile_size
        16
        >>> cfg.over_model_includes_proxies
        True
        >>> cfg.classify_mode(3, 2)  # U=3, V=2
        <Mode.LINEAR: 2>
    """

    def __init__(
        self,
        tile_size=16,
        over_model_includes_proxies=True,
        proxy_mask_mode="minmask",
        depth_eps_ft=0.01,
        tiny_max=2,
        thin_max=2,
    ):
        """Initialize VOP configuration.

        Args:
            tile_size: Tile size for early-out acceleration
            over_model_includes_proxies: Include proxy presence in "over model" check
            proxy_mask_mode: "edges" or "minmask" for proxy stamping
            depth_eps_ft: Depth tolerance for edge visibility (feet)
            tiny_max: Max dimension for TINY classification (cells)
            thin_max: Max thin dimension for LINEAR classification (cells)
        """
        self.tile_size = int(tile_size)
        self.over_model_includes_proxies = bool(over_model_includes_proxies)
        self.proxy_mask_mode = str(proxy_mask_mode)
        self.depth_eps_ft = float(depth_eps_ft)
        self.tiny_max = int(tiny_max)
        self.thin_max = int(thin_max)

        # Validate
        if self.tile_size <= 0:
            raise ValueError("tile_size must be positive")
        if self.proxy_mask_mode not in ("edges", "minmask"):
            raise ValueError("proxy_mask_mode must be 'edges' or 'minmask'")
        if self.depth_eps_ft < 0:
            raise ValueError("depth_eps_ft must be non-negative")
        if self.tiny_max < 0 or self.thin_max < 0:
            raise ValueError("tiny_max and thin_max must be non-negative")

    def __repr__(self):
        return (
            f"Config(tile_size={self.tile_size}, "
            f"over_model_includes_proxies={self.over_model_includes_proxies}, "
            f"proxy_mask_mode='{self.proxy_mask_mode}', "
            f"depth_eps_ft={self.depth_eps_ft}, "
            f"tiny_max={self.tiny_max}, thin_max={self.thin_max})"
        )

    def to_dict(self):
        """Export configuration as dictionary for JSON serialization."""
        return {
            "tile_size": self.tile_size,
            "over_model_includes_proxies": self.over_model_includes_proxies,
            "proxy_mask_mode": self.proxy_mask_mode,
            "depth_eps_ft": self.depth_eps_ft,
            "tiny_max": self.tiny_max,
            "thin_max": self.thin_max,
        }

    @classmethod
    def from_dict(cls, d):
        """Create Config from dictionary (e.g., from JSON)."""
        return cls(
            tile_size=d.get("tile_size", 16),
            over_model_includes_proxies=d.get("over_model_includes_proxies", True),
            proxy_mask_mode=d.get("proxy_mask_mode", "minmask"),
            depth_eps_ft=d.get("depth_eps_ft", 0.01),
            tiny_max=d.get("tiny_max", 2),
            thin_max=d.get("thin_max", 2),
        )
