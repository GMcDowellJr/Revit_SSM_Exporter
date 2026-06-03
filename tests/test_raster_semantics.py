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


def test_proxy_key_only_no_mask_counts_as_model_present():
    """A cell with model_proxy_key set but model_proxy_mask=False must not be empty."""
    bounds = Bounds2D(0.0, 0.0, 10.0, 10.0)
    r = ViewRaster(width=10, height=10, cell_size=1.0, bounds=bounds, tile_size=4)

    idx = r.get_cell_index(4, 4)
    assert idx is not None

    r.model_proxy_key[idx] = 7       # key stamped (e.g. by stamp_proxy_edge_idx)
    r.model_proxy_mask[idx] = False  # mask NOT set (the old failure mode)
    r.model_mask[idx] = False
    r.model_edge_key[idx] = -1

    assert r.has_model_proxy(idx) is True
    assert r.has_model_present(idx, mode="proxy") is True
    assert r.has_model_present(idx, mode="any") is True
    assert r.has_model_present(idx, mode="occ") is False


def test_proxy_key_only_no_occlusion_written():
    """Proxy-key-only cells must not have w_occ written (no occlusion authority)."""
    bounds = Bounds2D(0.0, 0.0, 10.0, 10.0)
    r = ViewRaster(width=10, height=10, cell_size=1.0, bounds=bounds, tile_size=4)

    idx = r.get_cell_index(5, 5)
    assert idx is not None

    r.model_proxy_key[idx] = 3
    r.model_proxy_mask[idx] = False
    r.model_mask[idx] = False

    # w_occ must remain inf — proxy never writes occlusion
    occ_val = r.w_occ[idx]
    assert occ_val == float("inf")
    assert r.has_model_occ(idx) is False


def test_stamp_proxy_edge_sets_proxy_mask():
    """stamp_proxy_edge_idx must set model_proxy_mask so has_model_proxy returns True."""
    bounds = Bounds2D(0.0, 0.0, 10.0, 10.0)
    r = ViewRaster(width=10, height=10, cell_size=1.0, bounds=bounds, tile_size=4)
    r.element_meta.append({"proxy_edge_cells": 0, "source_type": "HOST"})

    idx = r.get_cell_index(3, 3)
    assert idx is not None

    result = r.stamp_proxy_edge_idx(idx, key_index=0, depth=0.0)

    assert result is True
    assert r.model_proxy_key[idx] == 0
    assert bool(r.model_proxy_mask[idx])        # must be set by stamp
    assert r.has_model_proxy(idx) is True
    occ_val = r.w_occ[idx]
    assert occ_val == float("inf")              # no occlusion written
