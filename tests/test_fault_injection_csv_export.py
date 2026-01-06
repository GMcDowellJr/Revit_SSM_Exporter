# tests/test_fault_injection_csv_export.py

import pytest

from vop_interwoven.core.diagnostics import Diagnostics


def _minimal_pipeline_result():
    # Minimal shape that csv_export.export_pipeline_to_csv expects.
    # No Revit API objects required.
    return {
        "views": [
            {
                "view": None,
                "view_id": 101,
                "view_name": "UnitTest View",
                "elapsed_sec": 0.123,
                "raster": {
                    "width": 2,
                    "height": 2,
                    "cell_size_ft": 1.0,
                    "bounds_xy": {"xmin": 0, "ymin": 0, "xmax": 2, "ymax": 2},
                    # arrays used downstream
                    "anno_over_model": [False, False, False, False],
                    "anno_key": [-1, -1, -1, -1],
                    "anno_meta": [],
                    # other keys are tolerated / ignored by current code
                },
            }
        ]
    }


def test_csv_export_records_error_on_metrics_failure(monkeypatch, tmp_path):
    """
    Fault injection: metrics computation fails.
    Expectation: export remains non-silent and records an ERROR in diag before raising.
    """
    import vop_interwoven.csv_export as csv_export

    # Simulate the "CSV invariant failed" style error.
    def boom_metrics(_raster, *args, **kwargs):
        raise AssertionError("CSV invariant failed: TotalCells (4) != ... (3)")

    monkeypatch.setattr(csv_export, "compute_cell_metrics", boom_metrics, raising=True)

    diag = Diagnostics(max_events=50, capture_traceback=False)

    # Minimal config stub for hash computation
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

    with pytest.raises(AssertionError):
        csv_export.export_pipeline_to_csv(
            pipeline_result=_minimal_pipeline_result(),
            output_dir=str(tmp_path),
            config=Cfg(),
            doc=None,
            diag=diag,
        )

    d = diag.to_dict()
    # We only assert "an ERROR was recorded for export_csv" to avoid over-coupling callsite names.
    assert any(
        e.get("level") == "ERROR" and e.get("phase") == "export_csv"
        for e in d.get("events", [])
    ), "Expected an ERROR diagnostic event for metrics failure"


def test_csv_export_records_error_on_write_failure(monkeypatch, tmp_path):
    """
    Fault injection: CSV writing fails.
    Expectation: export remains non-silent and records an ERROR in diag before raising.
    """
    import vop_interwoven.csv_export as csv_export

    # Patch the underlying writer used by csv_export.export_pipeline_to_csv.
    import export.csv as export_csv_mod

    def boom_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(export_csv_mod, "_append_csv_rows", boom_write, raising=True)

    diag = Diagnostics(max_events=50, capture_traceback=False)

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

    with pytest.raises(OSError):
        csv_export.export_pipeline_to_csv(
            pipeline_result=_minimal_pipeline_result(),
            output_dir=str(tmp_path),
            config=Cfg(),
            doc=None,
            diag=diag,
        )

    d = diag.to_dict()
    assert any(
        e.get("level") == "ERROR" and e.get("phase") == "export_csv"
        for e in d.get("events", [])
    ), "Expected an ERROR diagnostic event for CSV write failure"
