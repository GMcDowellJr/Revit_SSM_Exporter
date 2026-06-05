"""
Persistent AREAL geometry cache backed by JSON file.

Replaces the in-memory LRUCache for areal_cache with a plain dict that
accumulates across runs.  The JSON file (vop_geometry_cache.json) is the
on-disk form; the in-memory dict IS the cache — no LRU eviction.
"""

import json
import os
import time


def _encode_key(key):
    """Encode tuple key to a JSON-compatible string using '|' separator."""
    return "|".join(str(k) for k in key)


def _decode_key(key_str):
    """Decode a '|'-separated string back to a typed tuple.

    Supports 3-element keys (areal_low_v1) and 4-element keys (areal_high_v1).
    The first element is always cast to int (element id).
    """
    parts = key_str.split("|")
    if len(parts) < 3:
        return None
    try:
        first = int(parts[0])
    except (ValueError, TypeError):
        return None
    return tuple([first] + parts[1:])


class GeometryCache:
    """Persistent geometry cache for AREAL HIGH/LOW-confidence face loops.

    Plain dict — no LRU eviction.  Backed by a JSON file in the output directory.

    Interface is backward-compatible with LRUCache.get()/set() so existing
    areal_cache call sites in pipeline.py require no changes beyond adding
    bbox_fingerprint to the HIGH-conf stored value.

    Fingerprint validation at load time: entries whose bbox_fingerprint differs
    from the current element's fingerprint are discarded silently.  Entries for
    elements not yet present in elem_cache are retained.

    Counters (hits, misses, disk_hits) reflect the current run only.
    """

    def __init__(self):
        self._data = {}
        self._loaded_keys = set()   # keys populated by load()
        self._dirty = False

        self._hits = 0
        self._misses = 0
        self._disk_hits = 0
        self._new_entries = 0
        self._loaded_entries = 0
        self._discarded_stale = 0

    # ------------------------------------------------------------------ #
    # LRUCache-compatible interface                                        #
    # ------------------------------------------------------------------ #

    @property
    def hits(self):
        return self._hits

    @property
    def misses(self):
        return self._misses

    @property
    def disk_hits(self):
        return self._disk_hits

    def get(self, key):
        """Return cached value or None.  Tracks hits/misses and disk_hits."""
        value = self._data.get(key)
        if value is not None:
            self._hits += 1
            if key in self._loaded_keys:
                self._disk_hits += 1
            return value
        self._misses += 1
        return None

    def set(self, key, value):
        """Store value in cache and mark dirty."""
        if key not in self._data:
            self._new_entries += 1
        self._data[key] = value
        self._dirty = True

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def load(self, cache_path, elem_cache=None):
        """Load entries from JSON.  Silent no-op if file is absent or unreadable.

        For each entry, if elem_cache provides a current bbox_fingerprint and it
        differs from the stored one, the entry is silently discarded (stale).
        Entries for elements not yet in elem_cache are always kept.

        Args:
            cache_path: Path to JSON file.
            elem_cache: Optional object with get(elem_id, source_id) → object
                        with a bbox_fingerprint attribute (6-float tuple/list).
        """
        if not os.path.exists(cache_path):
            return

        try:
            with open(cache_path, "r") as fh:
                payload = json.load(fh)
        except Exception:
            return

        if not isinstance(payload, dict):
            return
        if payload.get("schema") != "vop.geometry_cache.v1":
            return

        entries = payload.get("entries", {})
        if not isinstance(entries, dict):
            return

        for key_str, entry in entries.items():
            try:
                if not isinstance(entry, dict):
                    continue

                key = _decode_key(key_str)
                if key is None:
                    continue

                # Fingerprint validation — discard stale entries silently
                stored_bfp = entry.get("bbox_fingerprint")
                if stored_bfp is not None and elem_cache is not None:
                    try:
                        elem_id, source_id = key[0], key[1]
                        current = elem_cache.get(elem_id, source_id)
                        if current is not None:
                            current_bfp = getattr(current, "bbox_fingerprint", None)
                            if current_bfp is not None:
                                if tuple(stored_bfp) != tuple(current_bfp):
                                    self._discarded_stale += 1
                                    continue
                    except Exception:
                        pass  # keep entry if comparison cannot be performed

                self._data[key] = entry
                self._loaded_keys.add(key)
                self._loaded_entries += 1

            except Exception:
                continue

    def save(self, cache_path):
        """Write cache to JSON if dirty.  Silent no-op if not dirty.

        Creates the file on first write.  Does not date-stamp the filename —
        the same path accumulates entries across runs.
        """
        if not self._dirty:
            return

        try:
            entries = {}
            for key, value in self._data.items():
                key_str = _encode_key(key)
                entries[key_str] = value

            payload = {
                "schema": "vop.geometry_cache.v1",
                "saved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "entry_count": len(entries),
                "entries": entries,
            }

            dir_path = os.path.dirname(os.path.abspath(cache_path))
            os.makedirs(dir_path, exist_ok=True)

            tmp_path = cache_path + ".tmp"
            with open(tmp_path, "w") as fh:
                json.dump(payload, fh)
            os.replace(tmp_path, cache_path)

            self._dirty = False
        except Exception:
            pass  # never raise from save

    # ------------------------------------------------------------------ #
    # Statistics                                                           #
    # ------------------------------------------------------------------ #

    def stats(self):
        """Return dict with current-run counters and cache state."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "disk_hits": self._disk_hits,
            "new_entries": self._new_entries,
            "loaded_entries": self._loaded_entries,
            "discarded_stale": self._discarded_stale,
            "total_entries": len(self._data),
            "hit_rate": float(self._hits) / float(total) if total > 0 else 0.0,
        }
