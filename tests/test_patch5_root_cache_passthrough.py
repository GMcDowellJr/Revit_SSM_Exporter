from vop_interwoven.config import Config
from vop_interwoven.root_cache import extract_metrics_from_view_result


def test_extract_metrics_from_view_result_prefers_precomputed_metrics_without_raster_recompute():
    view_result = {
        "view_id": 10,
        "view_name": "v",
        "metrics": {
            "TotalCells": 4,
            "Cells_Empty": 3,
            "Cells_ModelOnly": 1,
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
            "CellSize_ft": 1.0,
        },
        "raster": {
            "cell_size_ft": 1.0,
            "bounds_xy": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
            "bounds_meta": {},
            "element_meta": [],
        },
    }

    metadata, metrics, element_summary, timings = extract_metrics_from_view_result(view_result, Config())
    assert metrics["TotalCells"] == 4
    assert metrics["Empty"] == 3
    assert metadata["CellSize_ft"] == 1.0
    assert element_summary["count"] == 0
    assert isinstance(timings, dict)
