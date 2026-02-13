from vop_interwoven.root_cache import extract_metrics_from_view_result
from vop_interwoven.csv_export import (
    get_perf_csv_header,
    get_vop_csv_header,
    view_result_to_perf_row,
    view_result_to_vop_row,
    view_result_to_core_row,
)


def test_perf_header_uses_pascal_case_and_includes_view_unique_id():
    header = get_perf_csv_header()
    assert "ViewId" in header
    assert "ViewUniqueId" in header
    assert "view_id" not in header
    assert "view_name" not in header


def test_vop_header_includes_view_unique_id():
    header = get_vop_csv_header()
    assert "ViewUniqueId" in header


def test_perf_row_includes_view_unique_id_from_result_or_view_object():
    row = view_result_to_perf_row(
        {
            "view_id": 42,
            "view_name": "V",
            "view_unique_id": "UID-42",
            "timings": {},
        },
        run_id="20260101T000000",
    )
    assert row["ViewId"] == 42
    assert row["ViewUniqueId"] == "UID-42"

    class _View:
        UniqueId = "UID-OBJ"

    row2 = view_result_to_perf_row(
        {
            "view_id": 43,
            "view_name": "V2",
            "view": _View(),
            "timings": {},
        },
        run_id="20260101T000000",
    )
    assert row2["ViewUniqueId"] == "UID-OBJ"


class _Cfg:
    tiny_max = 0
    thin_max = 0
    adaptive_tile_size = False
    proxy_mask_mode = ""
    over_model_includes_proxies = False
    tile_size = 0
    depth_eps_ft = 0.0
    anno_crop_margin_in = 0.0
    anno_expand_cap_cells = 0
    cell_size_paper_in = 0.0
    max_sheet_width_in = 0.0
    max_sheet_height_in = 0.0
    bounds_buffer_in = 0.0


def test_vop_row_uses_view_unique_id_without_view_object():
    row = view_result_to_vop_row(
        {
            "success": True,
            "view_id": 140929,
            "view_name": "Model QC-Level 1-Generic Element Check",
            "view_unique_id": "uid-140929",
            "metrics": {"TotalCells": 1, "Empty": 1, "ModelOnly": 0, "AnnoOnly": 0, "Overlap": 0},
            "raster": {},
        },
        config=_Cfg(),
        doc=None,
        run_id="20260101T000000",
    )
    assert row["ViewUniqueId"] == "uid-140929"


def test_core_row_uses_view_unique_id_without_view_object():
    row = view_result_to_core_row(
        {
            "success": True,
            "view_id": 140929,
            "view_name": "Model QC-Level 1-Generic Element Check",
            "view_unique_id": "uid-140929",
            "metrics": {"TotalCells": 1},
            "raster": {},
            "timings": {},
        },
        config=_Cfg(),
        doc=None,
        run_id="20260101T000000",
    )
    assert row is not None
    assert row["ViewUniqueId"] == "uid-140929"


def test_root_cache_metadata_carries_view_unique_id():
    class _Cfg:
        model_presence_mode = "ink"

    metadata, metrics, element_summary, timings = extract_metrics_from_view_result(
        {
            "view_id": 146925,
            "view_name": "SEA LEVEL",
            "view_unique_id": "64d3f457-04cb-481e-9da9-2f91f48e8823-00023ded",
            "view_type": "CeilingPlan",
            "raster": {
                "width": 1,
                "height": 1,
                "cell_size_ft": 1.0,
                "model_mask": [0],
                "anno_over_model": [0],
                "anno_key": [-1],
                "anno_meta": [],
                "element_meta": [],
            },
            "timings": {},
        },
        _Cfg(),
    )

    assert metadata["view_unique_id"] == "64d3f457-04cb-481e-9da9-2f91f48e8823-00023ded"

def test_vop_cache_row_uses_view_result_uid_when_cached_payload_missing_uid():
    row = view_result_to_vop_row(
        {
            "success": True,
            "from_cache": True,
            "view_id": 140929,
            "view_name": "Model QC-Level 1-Generic Element Check",
            "view_unique_id": "uid-140929",
            "row_payload": {
                "ViewId": 140929,
                "ViewName": "Model QC-Level 1-Generic Element Check",
                "ViewType": "FloorPlan",
            },
            "metrics": {"TotalCells": 1, "Empty": 1, "ModelOnly": 0, "AnnoOnly": 0, "Overlap": 0},
        },
        config=_Cfg(),
        doc=None,
        run_id="20260101T000000",
    )
    assert row["ViewUniqueId"] == "uid-140929"


def test_root_cache_row_payload_stores_uid_from_pascal_metadata(tmp_path):
    from vop_interwoven.root_cache import RootStyleCache

    rc = RootStyleCache(str(tmp_path), project_guid="test", exporter_version="vop_interwoven", config_hash="cfg")
    rc.set_view(
        view_id=123,
        signature="sig",
        metadata={"view_id": 123, "view_name": "V", "ViewUniqueId": "UID-123", "view_type": "FloorPlan"},
        metrics={"TotalCells": 1},
        element_summary={},
        timings={},
    )

    cached = rc.get_view_any(123)
    assert cached is not None
    payload = cached.get("row_payload", {})
    assert payload.get("view_unique_id") == "UID-123"
    assert payload.get("ViewUniqueId") == "UID-123"
