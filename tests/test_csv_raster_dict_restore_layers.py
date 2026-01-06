import pytest

from vop_interwoven.core.raster import ViewRaster
from vop_interwoven.core.math_utils import Bounds2D


def test_csv_restore_includes_edge_key_and_proxy_mask():
    bounds = Bounds2D(0.0, 0.0, 2.0, 2.0)
    r = ViewRaster(width=2, height=2, cell_size=1.0, bounds=bounds, tile_size=2)

    idx = r.get_cell_index(0, 0)
    r.model_mask[idx] = True
    r.model_edge_key[idx] = 7
    r.model_proxy_mask[idx] = True

    raster_dict = {
        # Minimal set required for reconstruction
        "W": r.W,
        "H": r.H,
        "cell_size": r.cell_size,

        # Layers we are guarding
        "model_mask": r.model_mask,
        "model_edge_key": r.model_edge_key,
        "model_proxy_mask": r.model_proxy_mask,

        # Present in typical raster dicts
        "anno_over_model": r.anno_over_model,

    }

    # Reconstruct with explicit bounds (avoid coupling to Bounds2D attribute names)
    rr = ViewRaster(
        width=raster_dict["W"],
        height=raster_dict["H"],
        cell_size=raster_dict["cell_size"],
        bounds=Bounds2D(0.0, 0.0, 2.0, 2.0),
        tile_size=2,
    )

    rr.model_mask = raster_dict.get("model_mask", [])
    rr.anno_over_model = raster_dict.get("anno_over_model", [])
   

    # PR4 required restores
    rr.model_edge_key = raster_dict.get("model_edge_key", [])
    rr.model_proxy_mask = raster_dict.get("model_proxy_mask", raster_dict.get("model_proxy_presence", []))

    assert rr.model_edge_key[idx] == 7
    assert rr.model_proxy_mask[idx] is True
