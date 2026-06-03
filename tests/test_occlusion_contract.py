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

    assert any(bool(m) for m in r.model_mask)


# ---------------------------------------------------------------------------
# rasterize_polygon_to_proxy: must NOT write w_occ (used by MEDIUM/LOW and TINY/LINEAR)
# ---------------------------------------------------------------------------

def test_polygon_to_proxy_no_w_occ():
    """rasterize_polygon_to_proxy must never write w_occ (proxy path)."""
    r = _make_raster()
    filled = r.rasterize_polygon_to_proxy(_SQUARE_LOOP, key_index=0, depth=1.0, source="HOST")

    assert filled > 0, "rasterize_polygon_to_proxy should fill cells"
    assert all(v == float("inf") for v in r.w_occ), "proxy fill must not write w_occ"


def test_polygon_to_proxy_respects_w_occ():
    """rasterize_polygon_to_proxy must skip cells where w_occ has a nearer element.

    Policy: all elements can be occluded; only AREAL+HIGH can occlude.
    Proxy fill must not write to cells already owned by a nearer occluder.
    """
    r = _make_raster()

    # Simulate AREAL+HIGH floor at depth=4 occupying a region
    floor_loop = [
        {"points": [(2.0, 2.0), (10.0, 2.0), (10.0, 10.0), (2.0, 10.0), (2.0, 2.0)],
         "is_hole": False}
    ]
    r.rasterize_silhouette_loops(floor_loop, key_index=0, depth=4.0, source="HOST")
    floor_cells = sum(1 for v in r.w_occ if v != float("inf"))
    assert floor_cells > 0, "floor must write w_occ"

    # Proxy element at depth=12.0 (farther from viewer) over same region
    r2_key = r.get_or_create_element_meta_index(
        elem_id=2, category="Walls", source_id="HOST", source_type="HOST"
    )
    wall_loop = [
        {"points": [(2.0, 2.0), (10.0, 2.0), (10.0, 10.0), (2.0, 10.0), (2.0, 2.0)],
         "is_hole": False}
    ]
    filled = r.rasterize_polygon_to_proxy(wall_loop, key_index=r2_key, depth=12.0, source="HOST")

    # Proxy fill must be zero: all cells in the wall's region are owned by the nearer floor
    assert filled == 0, (
        "proxy fill must be blocked by nearer w_occ (wall depth=12 > floor depth=4); "
        "got filled={}".format(filled)
    )

    # w_occ must be unchanged (proxy did not write to it)
    assert all(v == float("inf") or v == 4.0 for v in r.w_occ), \
        "proxy must not modify w_occ"


def test_polygon_to_proxy_writes_when_no_occluder():
    """rasterize_polygon_to_proxy must write normally when w_occ is empty."""
    r = _make_raster()
    # No prior w_occ writes
    loop = [
        {"points": [(2.0, 2.0), (8.0, 2.0), (8.0, 8.0), (2.0, 8.0), (2.0, 2.0)],
         "is_hole": False}
    ]
    filled = r.rasterize_polygon_to_proxy(loop, key_index=0, depth=12.0, source="HOST")
    assert filled > 0, "proxy fill must write when no occluder is present"


def test_polygon_to_proxy_writes_when_element_is_closer():
    """rasterize_polygon_to_proxy must write when this element is closer than w_occ."""
    r = _make_raster()
    # Farther element already in w_occ at depth=15
    loop = [
        {"points": [(2.0, 2.0), (8.0, 2.0), (8.0, 8.0), (2.0, 8.0), (2.0, 2.0)],
         "is_hole": False}
    ]
    r.rasterize_silhouette_loops(loop, key_index=0, depth=15.0, source="HOST")

    # Closer proxy element at depth=4 — should write (it is closer)
    r2_key = r.get_or_create_element_meta_index(
        elem_id=2, category="Walls", source_id="HOST", source_type="HOST"
    )
    filled = r.rasterize_polygon_to_proxy(loop, key_index=r2_key, depth=4.0, source="HOST")
    assert filled > 0, "proxy fill must write when it is closer than existing w_occ"


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


# ---------------------------------------------------------------------------
# Depth-gating: proxy writes must be blocked by a closer AREAL+HIGH occluder
#
# Policy: an element behind a floor (depth > w_occ) must not add proxy presence
# to cells already owned by the occluder. Both proxy fill (rasterize_polygon_to_proxy)
# and proxy edges (stamp_proxy_edge_idx) must enforce this gate.
# ---------------------------------------------------------------------------

_FLOOR_LOOP = [
    {"points": [(3.0, 3.0), (9.0, 3.0), (9.0, 9.0), (3.0, 9.0), (3.0, 3.0)], "is_hole": False}
]


def test_proxy_fill_blocked_when_behind_areal_high_occluder():
    """rasterize_polygon_to_proxy must not write to cells already held by a closer occluder."""
    r = _make_raster()
    # AREAL+HIGH floor at depth=4.0
    r.rasterize_silhouette_loops(_FLOOR_LOOP, key_index=0, depth=4.0, source="HOST")

    idx = r.get_cell_index(5, 5)
    w_after_floor = r.w_occ[idx]
    assert w_after_floor == pytest.approx(4.0), "precondition: floor wrote w_occ=4.0"
    assert r.model_mask[idx], "precondition: floor set model_mask"

    # TINY element at depth=14.5 (behind floor)
    r.rasterize_polygon_to_proxy(_FLOOR_LOOP, key_index=1, depth=14.5, source="HOST")

    w_after_proxy = r.w_occ[idx]
    assert w_after_proxy == pytest.approx(4.0), "proxy fill must not change w_occ"
    assert r.model_mask[idx], "proxy fill must not clear model_mask"
    assert r.model_proxy_key[idx] == -1, "proxy fill must not write key to occluded cell"
    assert not r.model_proxy_mask[idx], "proxy fill must not set proxy_mask on occluded cell"


def test_stamp_proxy_edge_blocked_when_behind_areal_high_occluder():
    """stamp_proxy_edge_idx must not write to cells already held by a closer occluder."""
    r = _make_raster()
    r.try_write_cell(5, 5, w_depth=4.0, source="HOST", key_index=0)

    idx = r.get_cell_index(5, 5)
    w_set = r.w_occ[idx]
    assert w_set == pytest.approx(4.0)

    result = r.stamp_proxy_edge_idx(idx, key_index=1, depth=14.5)

    assert not result, "stamp_proxy_edge_idx must return False when element is behind occluder"
    assert r.model_proxy_key[idx] == -1, "must not write proxy key when depth > w_occ"
    assert not r.model_proxy_mask[idx], "must not set proxy_mask when depth > w_occ"


def test_proxy_fill_allowed_when_no_occluder():
    """rasterize_polygon_to_proxy must write normally when the cell is empty (w_occ=inf)."""
    r = _make_raster()
    filled = r.rasterize_polygon_to_proxy(_FLOOR_LOOP, key_index=0, depth=14.5, source="HOST")

    assert filled > 0, "proxy fill must write when no occluder is present"
    idx = r.get_cell_index(5, 5)
    assert r.model_proxy_mask[idx], "proxy fill must set proxy_mask on empty cell"
    assert r.model_proxy_key[idx] == 0, "proxy fill must write key on empty cell"
    w_after = r.w_occ[idx]
    assert w_after == float("inf"), "proxy fill must not write w_occ even on empty cell"


def test_proxy_fill_allowed_when_at_same_depth_as_occluder():
    """Proxy fill at depth == w_occ must be allowed (coplanar elements share the cell)."""
    r = _make_raster()
    r.rasterize_silhouette_loops(_FLOOR_LOOP, key_index=0, depth=4.0, source="HOST")

    r.rasterize_polygon_to_proxy(_FLOOR_LOOP, key_index=1, depth=4.0, source="HOST")

    idx = r.get_cell_index(5, 5)
    assert r.model_proxy_key[idx] == 1, "proxy fill at same depth must be written"
    assert r.model_proxy_mask[idx], "proxy fill at same depth must set proxy_mask"


def test_areal_high_then_tiny_behind_floor_integration():
    """Integration: AREAL+HIGH floor occludes all cells; deeper TINY proxy must not appear on those cells.

    Scenario:
        floor  depth=4.0  AREAL+HIGH  → writes w_occ=4.0, model_mask=True
        handle depth=14.5 TINY        → must be entirely blocked on overlapping cells
    """
    r = _make_raster()
    r.get_or_create_element_meta_index(elem_id=2, category="Door", source_id="HOST", source_type="HOST")

    # Floor
    r.rasterize_silhouette_loops(_FLOOR_LOOP, key_index=0, depth=4.0, source="HOST")
    # TINY door handle occupying same cells, fully behind floor
    r.rasterize_polygon_to_proxy(_FLOOR_LOOP, key_index=1, depth=14.5, source="HOST")
    r.rasterize_closed_loops_to_proxy_edges(_FLOOR_LOOP, key_index=1, depth=14.5, source="HOST")

    idx = r.get_cell_index(5, 5)

    # Floor's contribution must be unchanged
    w_floor = r.w_occ[idx]
    assert w_floor == pytest.approx(4.0)
    assert r.model_mask[idx]
    assert r.has_model_present(idx, mode="occ")

    # Proxy of behind-floor TINY must not appear on floor's cells
    assert r.model_proxy_key[idx] == -1, "behind-floor TINY must not write proxy_key"
    assert not r.model_proxy_mask[idx], "behind-floor TINY must not set proxy_mask"
    assert not r.has_model_proxy(idx), "behind-floor TINY must not register proxy presence"
