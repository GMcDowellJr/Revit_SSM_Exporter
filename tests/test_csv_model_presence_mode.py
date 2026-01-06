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
