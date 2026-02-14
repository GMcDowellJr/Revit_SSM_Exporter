import json
from pathlib import Path
from types import SimpleNamespace

from vop_interwoven.config import Config
from vop_interwoven.csv_export import view_result_to_vop_row
from vop_interwoven.metrics.final_state_scanner import scan_final_state_totals
from vop_interwoven.metrics.manifest_evaluator import evaluate_metrics_manifest
from vop_interwoven.metrics_manifest import load_manifest_json


FIXTURE_DIR = Path("tests/fixtures/metrics_manifest_golden")


def _manifest_obj():
    cfg = Config()
    return load_manifest_json(cfg.metrics_manifest_path)


def _base_raster(edge, proxy, anno, anno_meta, element_meta):
    return SimpleNamespace(
        W=2,
        H=2,
        model_mask=[False, False, False, False],
        model_proxy_mask=[False, False, False, False],
        model_edge_key=edge,
        model_proxy_key=proxy,
        anno_key=anno,
        anno_meta=anno_meta,
        element_meta=element_meta,
        has_model_present=lambda idx, mode="any": (edge[idx] != -1 or proxy[idx] != -1),
    )


def _raster_model_anno():
    return _base_raster(
        edge=[0, 1, -1, -1],
        proxy=[-1, -1, -1, -1],
        anno=[-1, 0, 1, -1],
        anno_meta=[{"type": "TEXT"}, {"type": "TAG"}],
        element_meta=[
            {"source_type": "HOST", "category": "Walls", "class": "WALL"},
            {"source_type": "HOST", "category": "Doors", "class": "DOOR"},
        ],
    )


def _raster_anno_only():
    return _base_raster(
        edge=[-1, -1, -1, -1],
        proxy=[-1, -1, -1, -1],
        anno=[0, 1, 2, -1],
        anno_meta=[{"type": "TEXT"}, {"type": "TAG"}, {"type": "DIM"}],
        element_meta=[],
    )


def _raster_ext_overlap():
    return _base_raster(
        edge=[0, 1, 2, 3],
        proxy=[2, 3, -1, 0],
        anno=[0, -1, 1, -1],
        anno_meta=[{"type": "TEXT"}, {"type": "TAG"}],
        element_meta=[
            {"source_type": "DWG", "category": "Walls", "class": "WALL"},
            {"source_type": "DWG", "category": "Doors", "class": "DOOR"},
            {"source_type": "LINK", "category": "Stairs", "class": "STAIR"},
            {"source_type": "LINK", "category": "Columns", "class": "COLUMN"},
        ],
    )


def _load_expected(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _assert_invariants(totals):
    assert (
        totals["Cells_Empty"]
        + totals["Cells_ModelOnly"]
        + totals["Cells_AnnoOnly"]
        + totals["Cells_ExtOnly"]
        + totals["Cells_ModelAnno"]
        + totals["Cells_ModelExt"]
        + totals["Cells_AnnoExt"]
        + totals["Cells_All3"]
    ) == totals["TotalCells"]
    assert (
        totals["AnnoFinalCells_TEXT"]
        + totals["AnnoFinalCells_TAG"]
        + totals["AnnoFinalCells_DIM"]
        + totals["AnnoFinalCells_DETAIL"]
        + totals["AnnoFinalCells_LINES"]
        + totals["AnnoFinalCells_REGION"]
        + totals["AnnoFinalCells_OTHER"]
    ) == totals["AnnoPresentFinal"]


def test_patch7_golden_model_anno_fixture_matches_expected_and_validates():
    manifest_obj = _manifest_obj()
    totals = scan_final_state_totals(_raster_model_anno(), manifest_obj.data)
    assert totals == _load_expected("model_anno_expected.json")
    _assert_invariants(totals)

    validation = evaluate_metrics_manifest(
        totals,
        manifest_obj.data,
        manifest_sha256=manifest_obj.sha256,
        manifest_file_name=manifest_obj.file_name,
        available_primitives=["M", "A", "E", "AnnoType", "ExtType", "ModelClasses"],
        available_capabilities=[
            "source_partition_8",
            "count_by_anno_type_final",
            "count_by_ext_type_final_flags",
            "count_by_ext_type_final_intersection",
            "count_model_class_multihot",
        ],
        mode="warn",
    )
    assert validation["ok"] is True


def test_patch7_golden_anno_only_fixture_matches_expected_and_validates():
    manifest_obj = _manifest_obj()
    totals = scan_final_state_totals(_raster_anno_only(), manifest_obj.data)
    assert totals == _load_expected("anno_only_expected.json")
    _assert_invariants(totals)


def test_patch7_golden_ext_overlap_fixture_matches_expected_and_validates():
    manifest_obj = _manifest_obj()
    totals = scan_final_state_totals(_raster_ext_overlap(), manifest_obj.data)
    assert totals == _load_expected("ext_overlap_expected.json")
    _assert_invariants(totals)
    assert totals["ExtFinalCells_Any"] == (
        totals["ExtFinalCells_DWG"] + totals["ExtFinalCells_RVT"] - totals["ExtFinalCells_DWG_RVT"]
    )


def test_patch7_regression_vop_row_prefers_precomputed_metrics_over_raster_recompute():
    class _Cfg:
        csv_compat_mode = True
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

    # raster would imply TotalCells=4, but precomputed metrics assert TotalCells=9
    view_result = {
        "success": True,
        "view_id": 1,
        "view_name": "v",
        "raster": {
            "width": 2,
            "height": 2,
            "cell_size_ft": 1.0,
            "bounds_xy": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
            "model_edge_key": [-1, -1, -1, -1],
            "model_proxy_key": [-1, -1, -1, -1],
            "model_proxy_mask": [False, False, False, False],
            "model_mask": [False, False, False, False],
            "anno_over_model": [False, False, False, False],
            "anno_key": [-1, -1, -1, -1],
            "anno_meta": [],
            "element_meta": [],
        },
        "metrics": {
            "TotalCells": 9,
            "Cells_Empty": 9,
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

    row = view_result_to_vop_row(view_result, _Cfg(), doc=None, run_id="RUN")
    assert row["TotalCells"] == 9
