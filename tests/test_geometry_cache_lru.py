"""
Tests for GeometryCache (persistent AREAL geometry cache).

Covers:
- Basic get/set
- hits/misses counters
- Round-trip JSON persistence (save → load)
- Stale fingerprint detection on load
- Entries for unknown elements are kept on load
- disk_hits counter
- dirty-flag: save is a no-op when nothing was set
- stats() dict
"""

import json
import os
import tempfile

import pytest

from vop_interwoven.core.geometry_cache import GeometryCache, _encode_key, _decode_key


# ─────────────────────────────────────────────────────── helpers ──

def _make_value(bfp=(0., 0., 0., 5., 5., 3.), strategy="planar_face_loops", view_dir="z-"):
    return {
        "bbox_fingerprint": list(bfp),
        "strategy": strategy,
        "view_dir": view_dir,
        "loops": [{"points": [(1., 2., 3.)], "is_hole": False}],
    }


KEY_HIGH = (999, "HOST", "areal_high_v1", "z-")
KEY_LOW  = (888, "HOST", "areal_low_v1")


class _AlwaysMismatch:
    """elem_cache that always returns a different fingerprint."""
    def get(self, eid, sid):
        class _R:
            bbox_fingerprint = (9., 9., 9., 10., 10., 10.)
        return _R()


class _AlwaysMatch:
    """elem_cache that returns the same fingerprint as the stored entry."""
    def get(self, eid, sid):
        class _R:
            bbox_fingerprint = (0., 0., 0., 5., 5., 3.)
        return _R()


class _AlwaysNone:
    """elem_cache where the element is not yet known."""
    def get(self, eid, sid):
        return None


# ──────────────────────────────────────────────────── key codec ──

def test_encode_decode_4tuple():
    key = (12310296, "RVT_LINK:abc", "areal_high_v1", "z-")
    assert _decode_key(_encode_key(key)) == key


def test_encode_decode_3tuple():
    key = (99, "HOST", "areal_low_v1")
    assert _decode_key(_encode_key(key)) == key


def test_decode_bad_key_returns_none():
    assert _decode_key("bad") is None
    assert _decode_key("") is None
    assert _decode_key("a|b") is None


def test_decode_non_int_first_field():
    assert _decode_key("notanint|HOST|areal_high_v1|z-") is None


# ──────────────────────────────────────────────── basic cache ──

def test_get_miss():
    c = GeometryCache()
    assert c.get(KEY_HIGH) is None
    assert c.misses == 1
    assert c.hits == 0


def test_set_get():
    c = GeometryCache()
    v = _make_value()
    c.set(KEY_HIGH, v)
    result = c.get(KEY_HIGH)
    assert result is v
    assert c.hits == 1
    assert c.misses == 0


def test_set_marks_dirty():
    c = GeometryCache()
    assert not c._dirty
    c.set(KEY_HIGH, _make_value())
    assert c._dirty


def test_repeated_set_does_not_double_count_new_entries():
    c = GeometryCache()
    c.set(KEY_HIGH, _make_value())
    c.set(KEY_HIGH, _make_value())
    assert c._new_entries == 1


# ──────────────────────────────────────────── persistence ──

def test_round_trip(tmp_path):
    path = str(tmp_path / "gc.json")
    c = GeometryCache()
    c.set(KEY_HIGH, _make_value())
    c.save(path)

    c2 = GeometryCache()
    c2.load(path)
    result = c2.get(KEY_HIGH)
    assert result is not None
    assert result["strategy"] == "planar_face_loops"


def test_round_trip_3tuple_key(tmp_path):
    path = str(tmp_path / "gc.json")
    c = GeometryCache()
    v = {"confidence": "LOW", "strategy": "obb", "world_min": (0., 0., 0.), "world_max": (5., 5., 3.)}
    c.set(KEY_LOW, v)
    c.save(path)

    c2 = GeometryCache()
    c2.load(path)
    result = c2.get(KEY_LOW)
    assert result is not None
    assert result["strategy"] == "obb"


def test_save_creates_file(tmp_path):
    path = str(tmp_path / "gc.json")
    c = GeometryCache()
    c.set(KEY_HIGH, _make_value())
    c.save(path)
    assert os.path.exists(path)


def test_save_noop_when_not_dirty(tmp_path):
    path = str(tmp_path / "gc.json")
    c = GeometryCache()
    c.save(path)  # nothing set → no file
    assert not os.path.exists(path)


def test_load_absent_file_is_noop(tmp_path):
    path = str(tmp_path / "nonexistent.json")
    c = GeometryCache()
    c.load(path)  # should not raise
    assert c._loaded_entries == 0


def test_load_wrong_schema_is_ignored(tmp_path):
    path = str(tmp_path / "gc.json")
    with open(path, "w") as fh:
        json.dump({"schema": "wrong", "entries": {}}, fh)
    c = GeometryCache()
    c.load(path)
    assert c._loaded_entries == 0


def test_save_clears_dirty_flag(tmp_path):
    path = str(tmp_path / "gc.json")
    c = GeometryCache()
    c.set(KEY_HIGH, _make_value())
    assert c._dirty
    c.save(path)
    assert not c._dirty


def test_second_save_noop_if_not_dirty(tmp_path):
    path = str(tmp_path / "gc.json")
    c = GeometryCache()
    c.set(KEY_HIGH, _make_value())
    c.save(path)
    mtime1 = os.path.getmtime(path)

    import time; time.sleep(0.01)
    c.save(path)  # not dirty → should not touch file
    mtime2 = os.path.getmtime(path)
    assert mtime1 == mtime2


# ─────────────────────────────────────── fingerprint validation ──

def test_stale_fingerprint_discarded(tmp_path):
    path = str(tmp_path / "gc.json")

    c = GeometryCache()
    c.set(KEY_HIGH, _make_value(bfp=(0., 0., 0., 5., 5., 3.)))
    c.save(path)

    c2 = GeometryCache()
    c2.load(path, elem_cache=_AlwaysMismatch())
    assert c2.get(KEY_HIGH) is None
    assert c2._discarded_stale == 1


def test_matching_fingerprint_kept(tmp_path):
    path = str(tmp_path / "gc.json")

    c = GeometryCache()
    c.set(KEY_HIGH, _make_value(bfp=(0., 0., 0., 5., 5., 3.)))
    c.save(path)

    c2 = GeometryCache()
    c2.load(path, elem_cache=_AlwaysMatch())
    assert c2.get(KEY_HIGH) is not None
    assert c2._discarded_stale == 0


def test_unknown_element_kept(tmp_path):
    """Entry for element not in elem_cache is retained."""
    path = str(tmp_path / "gc.json")

    c = GeometryCache()
    c.set(KEY_HIGH, _make_value())
    c.save(path)

    c2 = GeometryCache()
    c2.load(path, elem_cache=_AlwaysNone())
    assert c2.get(KEY_HIGH) is not None


def test_no_elem_cache_all_entries_kept(tmp_path):
    path = str(tmp_path / "gc.json")

    c = GeometryCache()
    c.set(KEY_HIGH, _make_value())
    c.save(path)

    c2 = GeometryCache()
    c2.load(path, elem_cache=None)
    assert c2.get(KEY_HIGH) is not None


def test_entry_without_bbox_fingerprint_kept(tmp_path):
    """Entries that have no bbox_fingerprint field are always kept."""
    path = str(tmp_path / "gc.json")

    c = GeometryCache()
    v = {"strategy": "planar_face_loops", "loops": []}  # no bbox_fingerprint
    c.set(KEY_HIGH, v)
    c.save(path)

    c2 = GeometryCache()
    c2.load(path, elem_cache=_AlwaysMismatch())
    # No fingerprint stored → can't compare → keep
    assert c2.get(KEY_HIGH) is not None


def test_record_shaped_nested_high_fingerprint_discarded_when_stale(tmp_path):
    """Record-shaped AREAL entries validate nested HIGH fingerprints."""
    path = str(tmp_path / "gc.json")
    record = {
        "schema": "areal_geom_record_v1",
        "high_by_dir": {
            "z-": _make_value(bfp=(0., 0., 0., 5., 5., 3.)),
        },
    }

    c = GeometryCache()
    c.set(KEY_LOW, record)
    c.save(path)

    c2 = GeometryCache()
    c2.load(path, elem_cache=_AlwaysMismatch())
    assert c2.get(KEY_LOW) is None
    assert c2._discarded_stale == 1


def test_record_shaped_nested_high_fingerprint_kept_when_matching(tmp_path):
    path = str(tmp_path / "gc.json")
    record = {
        "schema": "areal_geom_record_v1",
        "high_by_dir": {
            "z-": _make_value(bfp=(0., 0., 0., 5., 5., 3.)),
        },
    }

    c = GeometryCache()
    c.set(KEY_LOW, record)
    c.save(path)

    c2 = GeometryCache()
    c2.load(path, elem_cache=_AlwaysMatch())
    assert c2.get(KEY_LOW) is not None
    assert c2._discarded_stale == 0


# ────────────────────────────────────────────────── disk_hits ──

def test_disk_hits_counter(tmp_path):
    path = str(tmp_path / "gc.json")

    c = GeometryCache()
    c.set(KEY_HIGH, _make_value())
    c.save(path)

    c2 = GeometryCache()
    c2.load(path)
    assert c2.disk_hits == 0
    c2.get(KEY_HIGH)
    assert c2.disk_hits == 1

    # A key set after load is NOT a disk_hit
    key2 = (111, "HOST", "areal_high_v1", "x+")
    c2.set(key2, _make_value())
    c2.get(key2)
    assert c2.disk_hits == 1  # unchanged


# ────────────────────────────────────────────────── stats ──

def test_stats(tmp_path):
    path = str(tmp_path / "gc.json")

    c = GeometryCache()
    c.set(KEY_HIGH, _make_value())
    c.save(path)

    c2 = GeometryCache()
    c2.load(path)
    c2.get(KEY_HIGH)
    c2.get((0, "HOST", "areal_high_v1", "x+"))  # miss

    s = c2.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["disk_hits"] == 1
    assert s["loaded_entries"] == 1
    assert s["new_entries"] == 0
    assert s["discarded_stale"] == 0
    assert s["hit_rate"] == 0.5


# ───────────────────────────────── task verification snippet ──

def test_task_verification_snippet(tmp_path):
    """Exact verification code from the PR-GC4 task description."""
    path = str(tmp_path / "test_geom_cache.json")

    cache = GeometryCache()
    key = (999, "HOST", "areal_high_v1", "z-")
    value = {
        "bbox_fingerprint": (0., 0., 0., 5., 5., 3.),
        "strategy": "planar_face_loops",
        "view_dir": "z-",
        "loops": [{"points": [(1., 2., 3.)], "is_hole": False}],
    }
    cache.set(key, value)
    cache.save(path)

    cache2 = GeometryCache()
    cache2.load(path)
    assert cache2.get(key) is not None
    assert cache2.get(key)["strategy"] == "planar_face_loops"

    class FakeElemCache:
        def get(self, eid, sid):
            class R:
                bbox_fingerprint = (9., 9., 9., 10., 10., 10.)
            return R()

    cache3 = GeometryCache()
    cache3.load(path, elem_cache=FakeElemCache())
    assert cache3.get(key) is None  # stale fingerprint discarded
