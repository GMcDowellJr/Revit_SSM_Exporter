import json

from vop_interwoven.core.element_cache import ElementCache, ElementFingerprint


def test_export_view_element_map_json_writes_view_index(tmp_path):
    cache = ElementCache(max_elements=10)
    cache.cache[(1001, "HOST")] = ElementFingerprint.from_dict(
        {
            "elem_id": 1001,
            "centroid": [0.0, 0.0, 0.0],
            "size": [1.0, 2.0, 3.0],
            "category": "Walls",
            "params": {},
        }
    )

    out = tmp_path / "view_analysis.json"
    view_elements = {
        2001: [(1001, "HOST"), (1001, "HOST"), (1002, "LINK_1")],
        2002: [(1002, "LINK_1")],
    }

    assert cache.export_view_element_map_json(str(out), view_elements=view_elements)

    payload = json.loads(out.read_text())
    assert payload["schema"] == "vop.view_element_map.v1"
    assert len(payload["views"]) == 2

    first = payload["views"][0]
    assert first["view_id"] == 2001
    assert first["element_ids"] == [1001, 1002]
    assert set(first.keys()) == {"view_id", "element_ids"}


def test_export_view_element_map_json_merges_existing_file(tmp_path):
    cache = ElementCache(max_elements=10)
    out = tmp_path / "view_map.json"

    existing = {
        "schema": "vop.view_element_map.v1",
        "generated_utc": 0,
        "views": [
            {"view_id": 2001, "element_ids": [1001]},
            {"view_id": 2002, "element_ids": [2001]},
        ],
    }
    out.write_text(json.dumps(existing))

    view_elements = {
        2001: [(1002, "HOST")],
        2003: [(3001, "HOST")],
    }

    assert cache.export_view_element_map_json(
        str(out),
        view_elements=view_elements,
        merge_existing=True,
    )

    payload = json.loads(out.read_text())
    views = {v["view_id"]: v["element_ids"] for v in payload["views"]}
    assert views[2001] == [1001, 1002]
    assert views[2002] == [2001]
    assert views[2003] == [3001]
