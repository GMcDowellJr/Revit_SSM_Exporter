from types import SimpleNamespace


def test_element_fingerprint_serializes_source_type():
    from vop_interwoven.core.element_cache import ElementFingerprint

    fp = ElementFingerprint(elem_id=1, bbox_model=None, category="Walls", source_type="LINK")
    payload = fp.to_dict()

    assert payload["source_type"] == "LINK"
    assert ":LINK:" in fp.to_signature_string()
    assert ElementFingerprint.from_dict(payload).source_type == "LINK"


def test_element_cache_export_analysis_csv_includes_source_type(tmp_path):
    from vop_interwoven.core.element_cache import ElementCache, ElementFingerprint

    cache = ElementCache(max_elements=10)
    cache.cache[(1, "RVT_LINK:test")] = ElementFingerprint(
        elem_id=1,
        bbox_model=None,
        category="Walls",
        source_type="LINK",
    )

    out = tmp_path / "elements.csv"
    assert cache.export_analysis_csv(str(out)) is True
    text = out.read_text()

    assert "source_type" in text.splitlines()[0]
    assert "LINK" in text


def test_element_cache_load_infers_source_type_for_legacy_link_entries(tmp_path):
    import json
    from vop_interwoven.core.element_cache import ElementCache

    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps({
        "elements": {
            "1:RVT_LINK:test": {
                "elem_id": 1,
                "centroid": [0.0, 0.0, 0.0],
                "size": [0.0, 0.0, 0.0],
                "category": "Walls",
                "params": {},
            }
        }
    }))

    cache = ElementCache.load_from_json(str(cache_file))
    assert cache.cache[(1, "RVT_LINK:test")].source_type == "LINK"


def test_element_cache_hit_upgrades_unknown_source_type():
    from vop_interwoven.core.element_cache import ElementCache, ElementFingerprint

    cache = ElementCache(max_elements=10)
    cache.cache[(1, "RVT_LINK:test")] = ElementFingerprint(
        elem_id=1,
        bbox_model=None,
        category="Walls",
        source_type="unknown",
    )

    fp = cache.get_or_create_fingerprint(
        elem=object(),
        elem_id=1,
        source_id="RVT_LINK:test",
        source_type="LINK",
    )

    assert fp.source_type == "LINK"
