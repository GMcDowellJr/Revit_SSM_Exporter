from vop_interwoven.core.math_utils import Bounds2D
from vop_interwoven.core.raster import ViewRaster


class _Cfg:
    tiny_max = 2
    thin_max = 2
    adaptive_tile_size = True
    proxy_mask_mode = "minmask"
    over_model_includes_proxies = True
    tile_size = 16
    depth_eps_ft = 0.01
    anno_crop_margin_in = 6.0
    anno_expand_cap_cells = 500
    cell_size_paper_in = 0.125
    max_sheet_width_in = 48.0
    max_sheet_height_in = 36.0
    bounds_buffer_in = 0.5
    model_presence_mode = "ink"


def _raster():
    r = ViewRaster(width=2, height=2, cell_size=1.0, bounds=Bounds2D(0, 0, 2, 2), tile_size=2)
    link_key = r.get_or_create_element_meta_index(
        elem_id=10,
        category="Walls",
        source_id="RVT_LINK:test",
        source_type="LINK",
    )
    host_key = r.get_or_create_element_meta_index(
        elem_id=20,
        category="Doors",
        source_id="HOST",
        source_type="HOST",
    )

    # Cell 0: linked model-only.  This deliberately uses element_meta index 0
    # to guard against treating key_index == 0 as falsy/no element.
    r.model_edge_key[0] = link_key

    # Cell 1: host model + annotation overlap.
    r.model_edge_key[1] = host_key
    anno_key = r.get_or_create_anno_meta_index(anno_id=30, anno_type="TEXT")
    r.anno_key[1] = anno_key

    # Cell 2: annotation-only.
    r.anno_key[2] = anno_key

    # Cell 3 remains empty.
    return r


def test_view_result_to_vop_row_recomputes_metrics_from_live_raster_when_missing():
    from vop_interwoven.csv_export import view_result_to_vop_row

    row = view_result_to_vop_row(
        {
            "success": True,
            "view_id": 1,
            "view_name": "fresh",
            "raster": _raster(),
        },
        _Cfg(),
        doc=None,
        run_id="RUN",
    )

    assert row["TotalCells"] == 4
    assert row["ModelOnly"] == 1
    assert row["AnnoOnly"] == 1
    assert row["Overlap"] == 1
    assert row["Empty"] == 1
    assert row["Ext_Cells_Any"] == 1
    assert row["Ext_Cells_RVT"] == 1


def test_extract_metrics_from_view_result_preserves_link_source_at_key_zero():
    from vop_interwoven.root_cache import extract_metrics_from_view_result

    _metadata, metrics, _element_summary, _timings = extract_metrics_from_view_result(
        {"success": True, "view_id": 1, "view_name": "fresh", "raster": _raster()},
        _Cfg(),
    )

    assert metrics["ModelOnly"] == 1
    assert metrics["Ext_Cells_Any"] == 1
    assert metrics["Ext_Cells_RVT"] == 1


def test_view_result_to_vop_row_ignores_incomplete_precomputed_metrics_with_live_raster():
    from vop_interwoven.csv_export import view_result_to_vop_row

    row = view_result_to_vop_row(
        {
            "success": True,
            "view_id": 1,
            "view_name": "fresh",
            "raster": _raster(),
            # Regression shape: a metrics stub with TotalCells only must not be
            # treated as authoritative for partition columns.
            "metrics": {"TotalCells": 4},
        },
        _Cfg(),
        doc=None,
        run_id="RUN",
    )

    assert row["TotalCells"] == 4
    assert row["ModelOnly"] == 1
    assert row["AnnoOnly"] == 1
    assert row["Overlap"] == 1
    assert row["Empty"] == 1
    assert row["Ext_Cells_Any"] == 1


def test_extract_metrics_from_view_result_ignores_incomplete_precomputed_metrics_with_live_raster():
    from vop_interwoven.root_cache import extract_metrics_from_view_result

    _metadata, metrics, _element_summary, _timings = extract_metrics_from_view_result(
        {
            "success": True,
            "view_id": 1,
            "view_name": "fresh",
            "raster": _raster(),
            "metrics": {"TotalCells": 4},
        },
        _Cfg(),
    )

    assert metrics["TotalCells"] == 4
    assert metrics["ModelOnly"] == 1
    assert metrics["AnnoOnly"] == 1
    assert metrics["Overlap"] == 1
    assert metrics["Empty"] == 1
    assert metrics["Ext_Cells_Any"] == 1
