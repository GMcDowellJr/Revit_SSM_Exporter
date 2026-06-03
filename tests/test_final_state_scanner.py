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


def test_scan_final_state_totals_uses_occupancy_layers_for_external_only():
    raster = SimpleNamespace(
        W=2,
        H=1,
        model_edge_key=[-1, -1],
        model_proxy_key=[-1, -1],
        anno_key=[-1, -1],
        anno_meta=[],
        element_meta=[],
        occ_host=[False, True],
        occ_link=[True, True],
        occ_dwg=[False, False],
        has_model_present=lambda idx, mode="any": True,
    )

    totals = scan_final_state_totals(raster, _manifest())

    assert totals["ExtFinalCells_Any"] == 2
    assert totals["ExtFinalCells_RVT"] == 2
    assert totals["ExtFinalCells_Only"] == 1
    assert totals["Cells_ModelExt"] == 2


def test_scan_final_state_totals_counts_host_occupancy_as_model_present():
    raster = SimpleNamespace(
        W=2,
        H=1,
        model_edge_key=[-1, -1],
        model_proxy_key=[-1, -1],
        anno_key=[-1, -1],
        anno_meta=[],
        element_meta=[],
        occ_host=[True, False],
        occ_link=[False, False],
        occ_dwg=[False, False],
        has_model_present=lambda idx, mode="any": False,
    )

    totals = scan_final_state_totals(raster, _manifest())

    assert totals["ModelClassCells_OTHER"] == 0
    assert totals["Cells_ModelOnly"] == 1
    assert totals["Cells_Empty"] == 1


def test_ext_cells_only_excludes_cells_with_host_and_link_overlap():
    """ExtFinalCells_Only must be zero when every ext cell also has host content.

    Mirrors the real-world scenario where an RVT-linked facade panel occupies
    the same grid cells as host structure — the LINK element won the depth test
    (it's closer) so occ_link=True and occ_host=True on every shared cell.
    """
    raster = SimpleNamespace(
        W=3,
        H=1,
        model_edge_key=[-1, -1, -1],
        model_proxy_key=[-1, -1, -1],
        anno_key=[-1, -1, -1],
        anno_meta=[],
        element_meta=[],
        # All three cells have both host and link presence (link won depth, host was prior write)
        occ_host=[True, True, True],
        occ_link=[True, True, True],
        occ_dwg=[False, False, False],
        has_model_present=lambda idx, mode="any": True,
    )

    totals = scan_final_state_totals(raster, _manifest())

    assert totals["ExtFinalCells_RVT"] == 3
    assert totals["ExtFinalCells_Only"] == 0   # host present in every ext cell
    assert totals["ExtFinalCells_Any"] == 3


def test_ext_cells_only_resets_do_not_falsely_elevate_count():
    """ExtFinalCells_Only must NOT count a cell just because occ_host was beaten.

    If try_write_cell reset occ_host when LINK won, the scanner would see
    occ_host=False and count the cell as ext-only even though host content exists.
    After removing the reset, occ_host stays True and the count is correct.
    """
    from vop_interwoven.core.raster import ViewRaster
    from vop_interwoven.core.math_utils import Bounds2D

    bounds = Bounds2D(0.0, 0.0, 5.0, 1.0)
    r = ViewRaster(width=5, height=1, cell_size=1.0, bounds=bounds, tile_size=4)
    r.get_or_create_element_meta_index(1, "Walls", "HOST", source_type="HOST")
    r.get_or_create_element_meta_index(2, "Panels", "LINK:doc1", source_type="LINK")

    # HOST writes all 5 cells at depth 5 (far)
    for col in range(5):
        r.try_write_cell(col, 0, w_depth=5.0, source="HOST")

    # LINK wins 3 cells at depth 2 (closer) — occ_host must stay True
    for col in range(3):
        r.try_write_cell(col, 0, w_depth=2.0, source="LINK")

    manifest = {"families": {"model_classes_multihot": {"enabled": False, "classes": []}}}
    totals = scan_final_state_totals(r, manifest)

    assert totals["ExtFinalCells_RVT"] == 3
    # All 3 ext cells also have host content → OnlyCount must be 0
    assert totals["ExtFinalCells_Only"] == 0


def test_ext_cells_only_host_loses_depth_still_records_spatial_presence():
    """occ_host must be True even when HOST loses the depth test to a closer LINK element.

    This is the primary production failure mode: linked facade panels are closer
    to the viewer than host walls, so LINK always wins depth. Before the fix,
    HOST's polygon rasterization only wrote occ_host for depth-winning cells —
    none — leaving every LINK cell incorrectly counted as ext-only.
    """
    from vop_interwoven.core.raster import ViewRaster
    from vop_interwoven.core.math_utils import Bounds2D

    bounds = Bounds2D(0.0, 0.0, 5.0, 1.0)
    r = ViewRaster(width=5, height=1, cell_size=1.0, bounds=bounds, tile_size=4)
    r.get_or_create_element_meta_index(1, "Walls", "HOST", source_type="HOST")
    r.get_or_create_element_meta_index(2, "Panels", "LINK:doc1", source_type="LINK")

    # LINK writes all 5 cells FIRST at depth 2 (closer)
    for col in range(5):
        r.try_write_cell(col, 0, w_depth=2.0, source="LINK")

    # HOST writes all 5 cells at depth 5 (farther) — loses depth test on every cell
    for col in range(5):
        r.try_write_cell(col, 0, w_depth=5.0, source="HOST")

    # Verify raw raster state: LINK won depth but HOST spatial presence recorded
    for col in range(5):
        idx = r.get_cell_index(col, 0)
        assert r.occ_link[idx] is True, f"col {col}: occ_link should be True"
        assert r.occ_host[idx] is True, f"col {col}: occ_host should be True (spatial, even though HOST lost)"

    manifest = {"families": {"model_classes_multihot": {"enabled": False, "classes": []}}}
    totals = scan_final_state_totals(r, manifest)

    assert totals["ExtFinalCells_RVT"] == 5
    assert totals["ExtFinalCells_Only"] == 0  # HOST present behind every LINK cell
