from vop_interwoven.core import silhouette


def test_reset_family_region_caches_clears_both_caches():
    silhouette._cache_set(silhouette._FAMILY_REGION_OUTLINE_CACHE, 101, {"xyz_loops": [1], "ts": 1.0})
    silhouette._cache_set(silhouette._FAMILY_FAMDOC_REGION_CACHE, 202, {"xyz_loops": [2], "ts": 2.0})

    silhouette.reset_family_region_caches()

    assert silhouette._cache_get(silhouette._FAMILY_REGION_OUTLINE_CACHE, 101) is None
    assert silhouette._cache_get(silhouette._FAMILY_FAMDOC_REGION_CACHE, 202) is None



def test_process_document_views_respects_reset_toggle(monkeypatch):
    from types import SimpleNamespace
    from vop_interwoven import pipeline

    calls = {"count": 0}

    def fake_reset():
        calls["count"] += 1

    monkeypatch.setattr(pipeline, "reset_family_region_caches", fake_reset)

    cfg = SimpleNamespace(
        date_override=None,
        output_dir=None,
        view_cache_enabled=False,
        view_cache_dir=None,
        view_cache_require_doc_unmodified=True,
    )

    pipeline.process_document_views(None, [], cfg, reset_family_caches=True)
    assert calls["count"] == 1

    pipeline.process_document_views(None, [], cfg, reset_family_caches=False)
    assert calls["count"] == 1


def test_process_document_views_streaming_resets_once_per_run(monkeypatch):
    from types import SimpleNamespace
    from vop_interwoven import streaming

    reset_calls = {"count": 0}
    per_view_flags = []

    def fake_reset():
        reset_calls["count"] += 1

    def fake_process_document_views(doc, view_ids, cfg, root_cache=None, reset_family_caches=True):
        per_view_flags.append(reset_family_caches)
        return [{
            "view_id": view_ids[0],
            "view_name": f"View {view_ids[0]}",
            "raster": object(),
            "success": True,
            "timings": {},
        }]

    monkeypatch.setattr("vop_interwoven.core.silhouette.reset_family_region_caches", fake_reset)
    monkeypatch.setattr("vop_interwoven.pipeline.process_document_views", fake_process_document_views)

    cfg = SimpleNamespace(retain_rasters_in_memory=True)
    seen = []

    out = streaming.process_document_views_streaming(
        doc=object(),
        view_ids=[11, 22, 33],
        cfg=cfg,
        on_view_complete=lambda result: seen.append(result["view_id"]),
        root_cache=None,
    )

    assert reset_calls["count"] == 1
    assert per_view_flags == [False, False, False]
    assert seen == [11, 22, 33]
    assert [item["view_id"] for item in out] == [11, 22, 33]
