"""
Occlusion contract tests.

Policy (from pipeline.py / README):
  - Only AREAL elements with HIGH-confidence geometry may write w_occ.
  - TINY elements: proxy ink only, no w_occ write.
  - LINEAR elements: proxy ink only, no w_occ write.
  - MEDIUM-confidence AREAL: proxy ink only, no w_occ write.
  - LOW-confidence AREAL: proxy ink only, no w_occ write.
  - OBB / AABB fallback: proxy edges only, no w_occ write.
  - proxy-only fallback paths: no w_occ write.

These tests exercise the raster methods directly (no Revit API required).
They encode the contract so regressions are caught immediately.
"""

import pytest

from vop_interwoven.core.raster import ViewRaster
from vop_interwoven.core.math_utils import Bounds2D


def _make_raster(W=12, H=12, cell_size=1.0):
    bounds = Bounds2D(0.0, 0.0, float(W) * cell_size, float(H) * cell_size)
    r = ViewRaster(width=W, height=H, cell_size=cell_size, bounds=bounds, tile_size=4)
    # Use the official factory so all required keys are present
    r.get_or_create_element_meta_index(
        elem_id=1, category="Test", source_id="HOST", source_type="HOST"
    )
    return r


_SQUARE_LOOP = [
    {"points": [(2.0, 2.0), (8.0, 2.0), (8.0, 8.0), (2.0, 8.0), (2.0, 2.0)], "is_hole": False}
]
_SQUARE_OPEN = [
    {"points": [(2.0, 2.0), (8.0, 2.0), (8.0, 8.0), (2.0, 8.0)], "open": True}
]


# ---------------------------------------------------------------------------
# AREAL + HIGH: must write w_occ (positive contract)
# ---------------------------------------------------------------------------

def test_areal_high_silhouette_writes_w_occ():
    """AREAL+HIGH path (rasterize_silhouette_loops) must update w_occ."""
    r = _make_raster()
    r.rasterize_silhouette_loops(_SQUARE_LOOP, key_index=0, depth=1.0, source="HOST")

    occ_written = any(v != float("inf") for v in r.w_occ)
    assert occ_written, "HIGH confidence silhouette must write w_occ"


def test_areal_high_silhouette_sets_model_mask():
    """AREAL+HIGH silhouette path must set model_mask for interior cells."""
    r = _make_raster()
    r.rasterize_silhouette_loops(_SQUARE_LOOP, key_index=0, depth=1.0, source="HOST")

    assert any(m is True for m in r.model_mask)


# ---------------------------------------------------------------------------
# rasterize_polygon_to_proxy: must NOT write w_occ (used by MEDIUM/LOW and TINY/LINEAR)
# ---------------------------------------------------------------------------

def test_polygon_to_proxy_no_w_occ():
    """rasterize_polygon_to_proxy must never write w_occ (proxy path)."""
    r = _make_raster()
    filled = r.rasterize_polygon_to_proxy(_SQUARE_LOOP, key_index=0, depth=1.0, source="HOST")

    assert filled > 0, "rasterize_polygon_to_proxy should fill cells"
    assert all(v == float("inf") for v in r.w_occ), "proxy fill must not write w_occ"


def test_polygon_to_proxy_writes_proxy_key():
    """rasterize_polygon_to_proxy must write model_proxy_key for interior cells."""
    r = _make_raster()
    r.rasterize_polygon_to_proxy(_SQUARE_LOOP, key_index=0, depth=1.0, source="HOST")

    assert any(k != -1 for k in r.model_proxy_key)


def test_polygon_to_proxy_no_model_edge_key():
    """rasterize_polygon_to_proxy must not touch model_edge_key."""
    r = _make_raster()
    r.rasterize_polygon_to_proxy(_SQUARE_LOOP, key_index=0, depth=1.0, source="HOST")

    assert all(k == -1 for k in r.model_edge_key)


# ---------------------------------------------------------------------------
# AREAL MEDIUM/LOW: proxy paths only, no w_occ
# ---------------------------------------------------------------------------

def test_areal_medium_proxy_edges_no_w_occ():
    """AREAL MEDIUM/LOW boundary ink (rasterize_closed_loops_to_proxy_edges) must not write w_occ."""
    r = _make_raster()
    r.rasterize_closed_loops_to_proxy_edges(_SQUARE_LOOP, key_index=0, depth=1.0, source="HOST")

    assert all(v == float("inf") for v in r.w_occ), "MEDIUM/LOW proxy edges must not write w_occ"
    assert any(k != -1 for k in r.model_proxy_key), "MEDIUM/LOW proxy edges must write proxy key"


def test_areal_low_open_polylines_to_proxy_no_w_occ():
    """AREAL LOW open polylines (rasterize_open_polylines_to_proxy_edges) must not write w_occ."""
    r = _make_raster()
    r.rasterize_open_polylines_to_proxy_edges(_SQUARE_OPEN, key_index=0, depth=1.0, source="HOST")

    assert all(v == float("inf") for v in r.w_occ), "proxy open polylines must not write w_occ"


# ---------------------------------------------------------------------------
# TINY/LINEAR contract: polygon_to_proxy + proxy edges, no w_occ
# ---------------------------------------------------------------------------

def test_tiny_proxy_fill_no_occlusion():
    """TINY element proxy fill must not write w_occ."""
    r = _make_raster()
    # Simulate a TINY element: a small 2x2 loop
    tiny_loop = [
        {"points": [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0), (1.0, 1.0)], "is_hole": False}
    ]
    filled = r.rasterize_polygon_to_proxy(tiny_loop, key_index=0, depth=0.5, source="HOST")
    r.rasterize_closed_loops_to_proxy_edges(tiny_loop, key_index=0, depth=0.5, source="HOST")

    assert all(v == float("inf") for v in r.w_occ), "TINY proxy must not write w_occ"
    # Must still be visible as proxy presence
    assert any(k != -1 for k in r.model_proxy_key), "TINY proxy must write proxy key"


def test_linear_proxy_fill_no_occlusion():
    """LINEAR element proxy fill must not write w_occ."""
    r = _make_raster()
    # Simulate a LINEAR element: a thin 1x8 loop
    linear_loop = [
        {"points": [(1.0, 4.0), (9.0, 4.0), (9.0, 5.0), (1.0, 5.0), (1.0, 4.0)], "is_hole": False}
    ]
    r.rasterize_polygon_to_proxy(linear_loop, key_index=0, depth=0.5, source="HOST")
    r.rasterize_closed_loops_to_proxy_edges(linear_loop, key_index=0, depth=0.5, source="HOST")

    assert all(v == float("inf") for v in r.w_occ), "LINEAR proxy must not write w_occ"
    assert any(k != -1 for k in r.model_proxy_key), "LINEAR proxy must write proxy key"


def test_linear_open_polyline_proxy_no_occlusion():
    """LINEAR open polyline proxy must not write w_occ."""
    r = _make_raster()
    open_loop = [
        {"points": [(1.0, 5.0), (9.0, 5.0)], "open": True}
    ]
    r.rasterize_open_polylines_to_proxy_edges(open_loop, key_index=0, depth=0.5, source="HOST")

    assert all(v == float("inf") for v in r.w_occ), "LINEAR open polyline proxy must not write w_occ"


# ---------------------------------------------------------------------------
# AABB fallback: stamp_proxy_edge_idx only, no w_occ
# ---------------------------------------------------------------------------

def test_aabb_fallback_stamp_proxy_edge_no_w_occ():
    """AABB fallback (stamp_proxy_edge_idx) must not write w_occ."""
    r = _make_raster()
    idx = r.get_cell_index(5, 5)
    assert idx is not None

    r.stamp_proxy_edge_idx(idx, key_index=0, depth=0.3)

    occ_after = r.w_occ[idx]
    assert occ_after == float("inf"), "AABB stamp_proxy_edge_idx must not write w_occ"
    assert r.model_proxy_key[idx] == 0, "AABB stamp must write model_proxy_key"
    assert r.has_model_proxy(idx), "AABB stamp must set model proxy presence"


# ---------------------------------------------------------------------------
# Proxy presence counts as model present (but not as occlusion)
# ---------------------------------------------------------------------------

def test_proxy_presence_counts_as_model_present():
    """Proxy-keyed cells must report has_model_present=True even with w_occ=inf."""
    r = _make_raster()
    r.rasterize_polygon_to_proxy(_SQUARE_LOOP, key_index=0, depth=1.0, source="HOST")

    interior_idx = r.get_cell_index(5, 5)
    assert interior_idx is not None

    # Proxy must be present
    assert r.has_model_proxy(interior_idx)
    # Occlusion must NOT be present
    occ_interior = r.w_occ[interior_idx]
    assert occ_interior == float("inf")
    assert not r.has_model_occ(interior_idx)


def test_proxy_edge_presence_counts_as_model_present_no_occlusion():
    """Proxy-edge-keyed cells must report proxy presence but not occlusion."""
    r = _make_raster()
    r.rasterize_closed_loops_to_proxy_edges(_SQUARE_LOOP, key_index=0, depth=1.0, source="HOST")

    edge_idx = r.get_cell_index(2, 2)  # boundary cell
    assert edge_idx is not None

    assert r.has_model_proxy(edge_idx)
    occ_edge = r.w_occ[edge_idx]
    assert occ_edge == float("inf")
    assert not r.has_model_occ(edge_idx)
