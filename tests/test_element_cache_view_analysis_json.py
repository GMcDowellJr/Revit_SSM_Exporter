import json

from vop_interwoven.core.element_cache import ElementCache, ElementFingerprint


def test_export_view_analysis_json_writes_view_index(tmp_path):
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

    assert cache.export_view_analysis_json(str(out), view_elements=view_elements)

    payload = json.loads(out.read_text())
    assert payload["schema"] == "vop.view_element_index.v1"
    assert len(payload["views"]) == 2

    first = payload["views"][0]
    assert first["view_id"] == 2001
    assert first["element_ids"] == [1001, 1002]
    assert first["element_refs"] == [
        {"elem_id": 1001, "source_id": "HOST"},
        {"elem_id": 1002, "source_id": "LINK_1"},
    ]
