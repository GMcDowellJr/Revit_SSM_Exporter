from vop_interwoven.core.cache import LRUCache


def test_geometry_cache_lru_eviction_order():
    c = LRUCache(max_items=2)

    c.set("a", 1)
    c.set("b", 2)
    assert len(c) == 2

    # Touch "a" so it becomes MRU
    assert c.get("a") == 1

    # Inserting "c" should evict LRU ("b")
    c.set("c", 3)

    assert c.get("b", default=None) is None
    assert c.get("a") == 1
    assert c.get("c") == 3
    assert c.evictions == 1


def test_geometry_cache_disabled_when_max_items_zero():
    c = LRUCache(max_items=0)
    c.set("a", 1)
    assert c.get("a", default=None) is None
    assert len(c) == 0
