from types import SimpleNamespace

from vop_interwoven.metrics.final_state_scanner import scan_final_state_totals


def _manifest():
    return {
        "families": {
            "model_classes_multihot": {
                "enabled": True,
                "classes": ["WALL", "DOOR", "STAIR", "COLUMN", "LIGHT", "OTHER"],
            }
        }
    }


def _raster_fixture():
    return SimpleNamespace(
        W=3,
        H=2,
        # model via edge/proxy/mask for has_model_present(..., mode="any")
        model_mask=[False, False, False, False, False, False],
        model_proxy_mask=[False, False, False, False, False, False],
        model_edge_key=[0, 1, -1, 3, 4, -1],
        model_proxy_key=[-1, -1, -1, -1, 5, 1],
        anno_key=[-1, -1, 0, 1, 2, 3],
        anno_meta=[
            {"type": "TEXT"},
            {"type": "TAG"},
            {"type": "LINES"},
            {"type": "REGION"},
        ],
        element_meta=[
            {"source_type": "HOST", "category": "Walls", "class": "WALL"},
            {"source_type": "DWG", "category": "Doors", "class": "DOOR"},
            {"source_type": "HOST", "category": "Stairs", "class": "STAIR"},
            {"source_type": "LINK", "category": "Columns", "class": "COLUMN"},
            {"source_type": "DWG", "category": "Lighting Fixtures", "class": "LIGHT"},
            {"source_type": "LINK", "category": "Generic Models", "class": "OTHER"},
        ],
        has_model_present=lambda idx, mode="any": (
            idx < 6
            and (
                (idx < 6 and [0, 1, -1, 3, 4, -1][idx] != -1)
                or (idx < 6 and [-1, -1, -1, -1, 5, 1][idx] != -1)
            )
        ),
    )


def test_scan_final_state_totals_partition_and_annotation_invariants():
    totals = scan_final_state_totals(_raster_fixture(), _manifest())

    assert totals["TotalCells"] == 6
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

    assert totals["AnnoPresentFinal"] == 4
    assert (
        totals["AnnoFinalCells_TEXT"]
        + totals["AnnoFinalCells_TAG"]
        + totals["AnnoFinalCells_DIM"]
        + totals["AnnoFinalCells_DETAIL"]
        + totals["AnnoFinalCells_LINES"]
        + totals["AnnoFinalCells_REGION"]
        + totals["AnnoFinalCells_OTHER"]
    ) == totals["AnnoPresentFinal"]


def test_scan_final_state_totals_ext_inclusion_exclusion_and_only():
    totals = scan_final_state_totals(_raster_fixture(), _manifest())

    assert totals["ExtFinalCells_DWG"] == 3
    assert totals["ExtFinalCells_RVT"] == 2
    assert totals["ExtFinalCells_DWG_RVT"] == 1
    assert totals["ExtFinalCells_Any"] == 4
    assert totals["ExtFinalCells_Only"] == 4
    assert totals["ExtFinalCells_Any"] == (
        totals["ExtFinalCells_DWG"] + totals["ExtFinalCells_RVT"] - totals["ExtFinalCells_DWG_RVT"]
    )


def test_scan_final_state_totals_model_class_multihot_dedup_per_cell():
    totals = scan_final_state_totals(_raster_fixture(), _manifest())

    assert totals["ModelClassCells_WALL"] == 1
    assert totals["ModelClassCells_DOOR"] == 2
    assert totals["ModelClassCells_COLUMN"] == 1
    assert totals["ModelClassCells_LIGHT"] == 1
    assert totals["ModelClassCells_OTHER"] == 1
