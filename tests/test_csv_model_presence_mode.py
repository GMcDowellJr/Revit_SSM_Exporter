import pytest

from vop_interwoven.core.raster import ViewRaster
from vop_interwoven.core.math_utils import Bounds2D
from vop_interwoven.csv_export import compute_cell_metrics


def _mk_raster():
    bounds = Bounds2D(0.0, 0.0, 2.0, 2.0)
    r = ViewRaster(width=2, height=2, cell_size=1.0, bounds=bounds, tile_size=2)
    return r


def test_csv_metrics_occ_ignores_edges():
    r = _mk_raster()
    idx = r.get_cell_index(0, 0)

    r.model_edge_key[idx] = 1      # edge only
    r.model_mask[idx] = False
    r.model_proxy_mask[idx] = False
    r.anno_over_model[idx] = False

    m = compute_cell_metrics(r, model_presence_mode="occ")
    assert m["ModelOnly"] == 0
    assert m["Empty"] == 4


def test_csv_metrics_edge_counts_edges():
    r = _mk_raster()
    idx = r.get_cell_index(0, 0)

    r.model_edge_key[idx] = 1      # edge only
    r.model_mask[idx] = False
    r.model_proxy_mask[idx] = False
    r.anno_over_model[idx] = False

    m = compute_cell_metrics(r, model_presence_mode="edge")
    assert m["ModelOnly"] == 1
    assert m["Empty"] == 3


def test_csv_metrics_ink_counts_occupancy_fill():
    r = _mk_raster()
    idx = r.get_cell_index(0, 0)

    r.model_edge_key[idx] = -1
    r.model_proxy_key[idx] = -1
    r.model_proxy_mask[idx] = False
    r.model_mask[idx] = True

    m = compute_cell_metrics(r, model_presence_mode="ink")
    assert m["ModelOnly"] == 1
    assert m["Empty"] == 3


def test_csv_metrics_ink_counts_proxy_key_only():
    """A cell with only model_proxy_key set (mask=False) must not be empty under ink mode."""
    r = _mk_raster()
    idx = r.get_cell_index(0, 0)

    r.model_edge_key[idx] = -1
    r.model_proxy_key[idx] = 2   # key set, no mask
    r.model_proxy_mask[idx] = False
    r.model_mask[idx] = False
    r.anno_over_model[idx] = False

    m = compute_cell_metrics(r, model_presence_mode="ink")
    assert m["ModelOnly"] == 1
    assert m["Empty"] == 3


def test_has_model_proxy_proxy_key_only():
    """ViewRaster.has_model_proxy must return True when only proxy_key is set."""
    r = _mk_raster()
    idx = r.get_cell_index(1, 0)

    r.model_proxy_key[idx] = 5
    r.model_proxy_mask[idx] = False
    r.model_mask[idx] = False
    r.model_edge_key[idx] = -1

    assert r.has_model_proxy(idx) is True
    assert r.has_model_present(idx, mode="proxy") is True
    assert r.has_model_present(idx, mode="any") is True
    assert r.has_model_present(idx, mode="occ") is False
