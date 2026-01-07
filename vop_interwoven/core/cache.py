"""Bounded LRU caches.

We use these caches to avoid repeated, expensive Revit geometry calls
within a single run (often across many views).

Design requirements:
  - Hard bounded size (no unbounded growth)
  - LRU eviction semantics
  - Safe under partial failures (cache must never crash the pipeline)
  - Minimal surface area (avoid semantic drift into a "global state" tool)
"""

from collections import OrderedDict


class LRUCache(object):
    """A simple, bounded LRU cache keyed by hashable keys.

    Notes:
        - max_items <= 0 disables caching (all gets miss, all sets no-op).
        - Values are stored as-is; callers must treat cached objects as immutable.
    """

    def __init__(self, max_items=0):
        try:
            self.max_items = int(max_items)
        except Exception:
            self.max_items = 0
        self._od = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def __len__(self):
        return len(self._od)

    def get(self, key, default=None):
        if self.max_items <= 0:
            self.misses += 1
            return default
        try:
            if key in self._od:
                val = self._od.pop(key)
                self._od[key] = val  # move to MRU
                self.hits += 1
                return val
            self.misses += 1
            return default
        except Exception:
            # Cache must never break callers.
            self.misses += 1
            return default

    def set(self, key, value):
        if self.max_items <= 0:
            return
        try:
            if key in self._od:
                try:
                    self._od.pop(key)
                except Exception:
                    pass
            self._od[key] = value

            # Evict LRU until size <= max_items
            while len(self._od) > self.max_items:
                try:
                    self._od.popitem(last=False)
                    self.evictions += 1
                except Exception:
                    break
        except Exception:
            # Never crash on cache writes.
            pass

    def clear(self):
        try:
            self._od.clear()
        except Exception:
            pass

    def stats(self):
        return {
            "max_items": self.max_items,
            "size": len(self._od),
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
        }
