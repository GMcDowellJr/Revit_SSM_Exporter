from vop_interwoven.config import Config
from vop_interwoven.csv_export import build_core_csv_row, get_core_csv_header, view_result_to_core_row


def test_get_core_csv_header_includes_cellsize_resolution_columns():
    header = get_core_csv_header()
    for col in [
        "CellSize_ft",
        "CellSizeRequested_ft",
        "CellSizeEffective_ft",
        "ResolutionMode",
        "CapTriggered",
        "AnnoExpanded",
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
                "anno_expanded": True,
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
    assert row["AnnoExpanded"] is True


def test_core_row_anno_expanded_defaults_false_when_absent():
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
                "cell_size_ft_requested": 0.5,
                "cell_size_ft_effective": 0.5,
                "resolution_mode": "canonical",
                "cap_triggered": False,
                # anno_expanded deliberately omitted, as an older/other
                # bounds_result producer (e.g. VIEW_MODE_ANNOTATION_ONLY's
                # inline bounds_result dict) might.
            },
        },
        "metrics": {"TotalCells": 1, "Cells_Empty": 1},
    }

    row = view_result_to_core_row(view_result, cfg, doc=None, run_id="RUN")
    assert row["AnnoExpanded"] is False


def test_core_row_anno_expanded_rehydrates_from_row_payload_on_cache_hit():
    # On a root-cache hit the result is metrics-only (no "raster" key at
    # all), so bounds_meta isn't available directly; AnnoExpanded must fall
    # back to the cached row_payload's own "AnnoExpanded"
    # (root_cache.extract_metrics_from_view_result() persists it there for
    # exactly this) rather than silently reporting False just because this
    # view happened to be served from cache.
    cfg = Config(csv_compat_mode=False)
    view_result = {
        "success": True,
        "view_id": 1,
        "view_name": "v",
        "metrics": {"TotalCells": 1, "Cells_Empty": 1},
        "row_payload": {"AnnoExpanded": True},
    }

    row = view_result_to_core_row(view_result, cfg, doc=None, run_id="RUN")
    assert row["AnnoExpanded"] is True


def test_batch_build_core_csv_row_includes_anno_expanded():
    # export_pipeline_to_csv()'s batch path is a separate code path from the
    # streaming views_result_to_core_row() above: it has its own core_headers
    # list and its own build_core_csv_row() row builder. Both must carry
    # AnnoExpanded, or the core CSV's columns depend on which exporter ran.
    run_info = {
        "date": "2026-01-01",
        "run_id": "RUN",
        "exporter_version": "vop_interwoven",
        "elapsed_sec": 0.0,
        "from_cache": False,
        "cell_size_ft": 0.5,
        "cell_size_ft_requested": 0.5,
        "cell_size_ft_effective": 0.5,
        "resolution_mode": "canonical",
        "cap_triggered": False,
        "anno_expanded": True,
    }

    row = build_core_csv_row(
        view=None, doc=None, metrics={}, config=Config(), run_info=run_info, view_metadata={}
    )
    header = get_core_csv_header()
    assert len(row) == len(header)
    assert row[header.index("AnnoExpanded")] is True
