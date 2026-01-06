import pytest

from vop_interwoven.core.raster import ViewRaster
from vop_interwoven.core.math_utils import Bounds2D


def test_model_present_semantics_edges_without_occ():
    bounds = Bounds2D(0.0, 0.0, 10.0, 10.0)
    r = ViewRaster(width=10, height=10, cell_size=1.0, bounds=bounds, tile_size=4)

    idx = r.get_cell_index(2, 3)
    assert idx is not None

    # Edge only
    r.model_edge_key[idx] = 0
    # No interior occ
    r.model_mask[idx] = False
    # No proxy
    r.model_proxy_mask[idx] = False

    assert r.has_model_edge(idx) is True
    assert r.has_model_occ(idx) is False
    assert r.has_model_proxy(idx) is False

    assert r.has_model_present(idx, mode="edge") is True
    assert r.has_model_present(idx, mode="occ") is False
    assert r.has_model_present(idx, mode="proxy") is False
    assert r.has_model_present(idx, mode="any") is True


def test_model_present_semantics_proxy_without_occ_or_edge():
    bounds = Bounds2D(0.0, 0.0, 10.0, 10.0)
    r = ViewRaster(width=10, height=10, cell_size=1.0, bounds=bounds, tile_size=4)

    idx = r.get_cell_index(1, 1)
    assert idx is not None

    r.model_proxy_mask[idx] = True
    r.model_mask[idx] = False
    r.model_edge_key[idx] = -1

    assert r.has_model_proxy(idx) is True
    assert r.has_model_present(idx, mode="proxy") is True
    assert r.has_model_present(idx, mode="occ") is False
    assert r.has_model_present(idx, mode="edge") is False
    assert r.has_model_present(idx, mode="any") is True
