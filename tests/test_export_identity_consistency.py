from vop_interwoven.csv_export import (
    get_perf_csv_header,
    get_vop_csv_header,
    view_result_to_perf_row,
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


def test_root_cache_payload_persists_view_unique_id(tmp_path):
    from vop_interwoven.root_cache import RootStyleCache

    cache = RootStyleCache(
        output_dir=str(tmp_path),
        project_guid="proj",
        exporter_version="vop_interwoven",
        config_hash="cfg",
    )

    cache.set_view(
        view_id=7,
        signature="sig",
        metadata={
            "view_id": 7,
            "view_name": "View 7",
            "view_type": "FloorPlan",
            "view_unique_id": "UID-7",
        },
        metrics={"TotalCells": 1},
        element_summary={},
        timings={},
    )

    cached = cache.get_view_any(7)
    payload = cached["row_payload"]
    assert payload["view_unique_id"] == "UID-7"
    assert payload["ViewUniqueId"] == "UID-7"
