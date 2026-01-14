def test_vop_cache_hit_viewtype_is_human_readable_and_blanks_preserved():
    from vop_interwoven.csv_export import view_result_to_vop_row

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

    cfg = _Cfg()

    view_result = {
        "success": True,
        "from_cache": True,
        "view_id": 123,
        "view_name": "Level 1",
        "row_payload": {
            "view_type": "FloorPlan",
            "discipline": "",
            "phase": "",
            "sheet_number": "",
            "view_template_name": "",
        },
        "metrics": {
            "TotalCells": 100,
            "Empty": 80,
            "ModelOnly": 10,
            "AnnoOnly": 5,
            "Overlap": 5,
        },
    }

    row = view_result_to_vop_row(
        view_result=view_result,
        config=cfg,
        doc=None,
        run_id="TEST_RUN",
    )

    assert isinstance(row.get("ViewType"), str)
    assert row["ViewType"] != ""
    assert not row["ViewType"].isdigit()

    assert row.get("Discipline", "") == ""
    assert row.get("Phase", "") == ""
    assert row.get("SheetNumber", "") == ""
    assert row.get("ViewTemplate_Name", "") == ""

    assert row.get("FromCache") == "Y"
