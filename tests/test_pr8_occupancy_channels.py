import pytest

from vop_interwoven.core.raster import ViewRaster
from vop_interwoven.core.math_utils import Bounds2D


def _make_raster(W=10, H=10, cell_size=1.0):
    bounds = Bounds2D(0.0, 0.0, float(W) * cell_size, float(H) * cell_size)
    return ViewRaster(width=W, height=H, cell_size=cell_size, bounds=bounds, tile_size=4)


def test_proxy_fill_affects_occlusion_not_model_ink_by_default():
    r = _make_raster()
    ki = r.get_or_create_element_meta_index(elem_id=1, category="Floors", source_id="HOST", source_type="HOST")

    # A simple square proxy loop in UV-space (closed)
    loops = [{"points": [(1.0, 1.0), (8.0, 1.0), (8.0, 8.0), (1.0, 8.0), (1.0, 1.0)], "is_hole": False}]
    filled = r.rasterize_proxy_loops(loops, ki, depth=0.0, source="HOST", write_proxy_edges=False)

    assert filled > 0
    assert any(m is True for m in r.model_mask)  # occlusion/interior coverage
    assert all(k == -1 for k in r.model_edge_key)  # no model ink edges
    assert all(k == -1 for k in r.model_proxy_key)  # no proxy edges when disabled


def test_proxy_edges_enabled_perimeter_only_to_proxy_channel():
    r = _make_raster(W=12, H=12)
    ki = r.get_or_create_element_meta_index(elem_id=2, category="Floors", source_id="HOST", source_type="HOST")

    loops = [{"points": [(2.0, 2.0), (9.0, 2.0), (9.0, 9.0), (2.0, 9.0), (2.0, 2.0)], "is_hole": False}]
    r.rasterize_proxy_loops(loops, ki, depth=0.0, source="HOST", write_proxy_edges=True)

    # Proxy edges exist
    assert any(k != -1 for k in r.model_proxy_key)

    # Model ink still untouched
    assert all(k == -1 for k in r.model_edge_key)

    # Interior cell should not be marked as proxy edge
    interior_idx = r.get_cell_index(5, 5)
    assert r.model_proxy_key[interior_idx] == -1


def test_real_silhouette_writes_model_ink_edges():
    r = _make_raster(W=12, H=12)
    ki = r.get_or_create_element_meta_index(elem_id=3, category="Walls", source_id="HOST", source_type="HOST")

    loops = [{"points": [(2.0, 2.0), (9.0, 2.0), (9.0, 9.0), (2.0, 9.0), (2.0, 2.0)], "is_hole": False}]
    r.rasterize_silhouette_loops(loops, ki, depth=0.0, source="HOST")

    assert any(k != -1 for k in r.model_edge_key)
