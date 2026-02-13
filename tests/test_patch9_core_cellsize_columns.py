from vop_interwoven.config import Config
from vop_interwoven.csv_export import get_core_csv_header, view_result_to_core_row


def test_get_core_csv_header_includes_cellsize_resolution_columns():
    header = get_core_csv_header()
    for col in [
        "CellSize_ft",
        "CellSizeRequested_ft",
        "CellSizeEffective_ft",
        "ResolutionMode",
        "CapTriggered",
    ]:
        assert col in header


def test_core_row_contains_cellsize_resolution_values():
    cfg = Config(csv_compat_mode=False)
    view_result = {
        "success": True,
        "view_id": 1,
        "view_name": "v",
        "raster": {
            "width": 1,
            "height": 1,
            "cell_size_ft": 0.5,
            "bounds_xy": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
            "bounds_meta": {
                "cell_size_ft_requested": 0.6,
                "cell_size_ft_effective": 0.5,
                "resolution_mode": "canonical",
                "cap_triggered": True,
            },
        },
        "metrics": {"TotalCells": 1, "Cells_Empty": 1},
    }

    row = view_result_to_core_row(view_result, cfg, doc=None, run_id="RUN")
    assert row["CellSize_ft"] == 0.5
    assert row["CellSizeRequested_ft"] == 0.6
    assert row["CellSizeEffective_ft"] == 0.5
    assert row["ResolutionMode"] == "canonical"
    assert row["CapTriggered"] is True
