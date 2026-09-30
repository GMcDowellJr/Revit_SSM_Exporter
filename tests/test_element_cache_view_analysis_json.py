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
        "run_id": "RUN_A",
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
        run_id="RUN_A",
    )

    payload = json.loads(out.read_text())
    assert payload["run_id"] == "RUN_A"
    views = {v["view_id"]: v["element_ids"] for v in payload["views"]}
    assert views[2001] == [1001, 1002]
    assert views[2002] == [2001]
    assert views[2003] == [3001]


def test_a_prior_runs_map_is_replaced_not_merged(tmp_path):
    """Codex, PR #221: merge_existing unioned whatever map a PREVIOUS run left
    in the same folder and date, so removed elements and unrequested views
    survived into this run's map. Only a file stamped with this run's id is
    merged; a different id, or an unstamped (pre-fix) file, is replaced."""
    cache = ElementCache(max_elements=10)
    out = tmp_path / "view_map.json"
    for stale in ({"run_id": "RUN_OLD"}, {}):
        prior = dict(stale, schema="vop.view_element_map.v1", generated_utc=0,
                     views=[{"view_id": 2001, "element_ids": [999]},
                            {"view_id": 2002, "element_ids": [2001]}])
        out.write_text(json.dumps(prior))
        assert cache.export_view_element_map_json(
            str(out), view_elements={2001: [(1002, "HOST")]},
            merge_existing=True, run_id="RUN_NEW")
        payload = json.loads(out.read_text())
        assert payload["run_id"] == "RUN_NEW"
        assert {v["view_id"]: v["element_ids"] for v in payload["views"]} == {2001: [1002]}


def test_the_streaming_exporter_scopes_the_map_to_its_run(tmp_path):
    """The pipeline reads the run id off cfg; the exporter is what sets it."""
    from vop_interwoven.config import Config
    from vop_interwoven.streaming import StreamingExporter
    cfg = Config()
    exp = StreamingExporter(str(tmp_path), cfg, doc=None, export_png=False,
                            export_csv=False, run_id="RUN_X")
    assert cfg._view_element_map_run_id == exp.run_id == "RUN_X"


def test_without_a_run_id_the_map_still_merges():
    """A caller with no StreamingExporter passes no run id; each per-view
    call must still add to the map rather than replace it."""
    import tempfile, os
    cache = ElementCache(max_elements=10)
    out = os.path.join(tempfile.mkdtemp(), "view_map.json")
    cache.export_view_element_map_json(out, view_elements={1: [(10, "HOST")]}, merge_existing=True)
    cache.export_view_element_map_json(out, view_elements={2: [(20, "HOST")]}, merge_existing=True)
    views = {v["view_id"]: v["element_ids"] for v in json.loads(open(out).read())["views"]}
    assert views == {1: [10], 2: [20]}


def test_the_pipeline_passes_the_exporters_run_id_to_the_map():
    """Pinned on the source: without it every streaming per-view call would
    be merged as run-less, and a prior run's map would leak back in."""
    import os
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "vop_interwoven", "pipeline.py")).read()
    call = src[src.index("export_view_element_map_json("):]
    call = call[:call.index("\n            )")]
    assert 'run_id=getattr(cfg, "_view_element_map_run_id", None)' in call
