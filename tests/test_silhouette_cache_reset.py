from vop_interwoven.core import silhouette


def test_reset_family_region_caches_clears_both_caches():
    silhouette._cache_set(silhouette._FAMILY_REGION_OUTLINE_CACHE, 101, {"xyz_loops": [1], "ts": 1.0})
    silhouette._cache_set(silhouette._FAMILY_FAMDOC_REGION_CACHE, 202, {"xyz_loops": [2], "ts": 2.0})

    silhouette.reset_family_region_caches()

    assert silhouette._cache_get(silhouette._FAMILY_REGION_OUTLINE_CACHE, 101) is None
    assert silhouette._cache_get(silhouette._FAMILY_FAMDOC_REGION_CACHE, 202) is None
