import pytest


def test_view_result_to_vop_row_prefers_precomputed_metrics(monkeypatch):
    import vop_interwoven.csv_export as csv_export

    def _boom(*args, **kwargs):
        raise AssertionError("should not recompute")

    monkeypatch.setattr(csv_export, "compute_cell_metrics", _boom, raising=True)
    monkeypatch.setattr(csv_export, "compute_annotation_type_metrics", _boom, raising=True)
    monkeypatch.setattr(csv_export, "compute_external_cell_metrics", _boom, raising=True)

    class Cfg:
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

    view_result = {
        "success": True,
        "view_id": 1,
        "view_name": "v",
        "raster": {"width": 1, "height": 1, "cell_size_ft": 1.0, "bounds_xy": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1}},
        "metrics": {
            "TotalCells": 1,
            "Cells_Empty": 1,
            "Cells_ModelOnly": 0,
            "Cells_AnnoOnly": 0,
            "Cells_ModelAnno": 0,
            "ExtFinalCells_Any": 0,
            "ExtFinalCells_Only": 0,
            "ExtFinalCells_DWG": 0,
            "ExtFinalCells_RVT": 0,
            "AnnoFinalCells_TEXT": 0,
            "AnnoFinalCells_TAG": 0,
            "AnnoFinalCells_DIM": 0,
            "AnnoFinalCells_DETAIL": 0,
            "AnnoFinalCells_LINES": 0,
            "AnnoFinalCells_REGION": 0,
            "AnnoFinalCells_OTHER": 0,
        },
    }

    row = csv_export.view_result_to_vop_row(view_result, Cfg(), doc=None, run_id="RUN")
    assert row["TotalCells"] == 1
    assert row["Empty"] == 1


def test_export_pipeline_to_csv_prefers_precomputed_metrics(monkeypatch, tmp_path):
    import vop_interwoven.csv_export as csv_export

    def _boom(*args, **kwargs):
        raise AssertionError("should not recompute")

    monkeypatch.setattr(csv_export, "compute_cell_metrics", _boom, raising=True)
    monkeypatch.setattr(csv_export, "compute_annotation_type_metrics", _boom, raising=True)
    monkeypatch.setattr(csv_export, "compute_external_cell_metrics", _boom, raising=True)

    class Cfg:
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
        output_dir = str(tmp_path)

    pipeline_result = {
        "views": [
            {
                "success": True,
                "view_id": 1,
                "view_name": "v",
                "raster": {"width": 1, "height": 1, "cell_size_ft": 1.0, "bounds_xy": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1}},
                "metrics": {
                    "TotalCells": 1,
                    "Cells_Empty": 1,
                    "Cells_ModelOnly": 0,
                    "Cells_AnnoOnly": 0,
                    "Cells_ModelAnno": 0,
                    "ExtFinalCells_Any": 0,
                    "ExtFinalCells_Only": 0,
                    "ExtFinalCells_DWG": 0,
                    "ExtFinalCells_RVT": 0,
                    "AnnoFinalCells_TEXT": 0,
                    "AnnoFinalCells_TAG": 0,
                    "AnnoFinalCells_DIM": 0,
                    "AnnoFinalCells_DETAIL": 0,
                    "AnnoFinalCells_LINES": 0,
                    "AnnoFinalCells_REGION": 0,
                    "AnnoFinalCells_OTHER": 0,
                },
            }
        ]
    }

    out = csv_export.export_pipeline_to_csv(pipeline_result, str(tmp_path), Cfg(), doc=None)
    assert "vop_csv_path" in out
