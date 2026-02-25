"""Memory telemetry helpers for Dynamo/Python.NET runtime."""

from __future__ import annotations

import csv
import time
from datetime import datetime


def _get_memory_mb():
    """Return (working_set_mb, private_bytes_mb) using CLR Process APIs."""
    try:
        import System
        from System.Diagnostics import Process

        proc = Process.GetCurrentProcess()
        proc.Refresh()
        ws_mb = float(proc.WorkingSet64) / (1024.0 * 1024.0)
        priv_mb = float(proc.PrivateMemorySize64) / (1024.0 * 1024.0)
        return ws_mb, priv_mb
    except Exception as e:
        print("[WARN] memory_telemetry._get_memory_mb failed: {}".format(e))
        return None, None


def _force_clr_gc():
    """Force CLR collection using 3-call pattern and return delta stats."""
    result = {"pre_priv_mb": None, "post_priv_mb": None, "freed_mb": None}
    try:
        import System

        _ws0, pre_priv = _get_memory_mb()
        result["pre_priv_mb"] = pre_priv

        System.GC.Collect()
        System.GC.WaitForPendingFinalizers()
        System.GC.Collect()

        _ws1, post_priv = _get_memory_mb()
        result["post_priv_mb"] = post_priv

        if (pre_priv is not None) and (post_priv is not None):
            result["freed_mb"] = float(pre_priv) - float(post_priv)
        else:
            result["freed_mb"] = None
    except Exception as e:
        print("[WARN] memory_telemetry._force_clr_gc failed: {}".format(e))
    return result


class MemoryTracker(object):
    def __init__(self):
        self._t0 = time.perf_counter()
        self._records = []
        self.gc_freed_total_mb = 0.0
        self.gc_call_count = 0

    def mark(self, label):
        rec = {
            "label": str(label),
            "elapsed_s": round(float(time.perf_counter() - self._t0), 6),
            "wall_time": datetime.now().isoformat(),
            "ws_mb": None,
            "priv_mb": None,
        }
        try:
            ws_mb, priv_mb = _get_memory_mb()
            rec["ws_mb"] = ws_mb
            rec["priv_mb"] = priv_mb
        except Exception as e:
            print("[WARN] MemoryTracker.mark failed: {}".format(e))
        self._records.append(rec)
        return rec

    def mark_and_gc(self, label):
        try:
            gc_stats = _force_clr_gc()
            self.gc_call_count += 1
            freed = gc_stats.get("freed_mb")
            if freed is not None:
                self.gc_freed_total_mb += float(freed)
        except Exception as e:
            print("[WARN] MemoryTracker.mark_and_gc GC failed: {}".format(e))
        return self.mark("{} [post-GC]".format(label))

    def delta(self, label_a, label_b, field="priv_mb"):
        try:
            a = None
            b = None
            for r in self._records:
                if r.get("label") == label_a and a is None:
                    a = r
                if r.get("label") == label_b and b is None:
                    b = r
            if (a is None) or (b is None):
                return None
            va = a.get(field)
            vb = b.get(field)
            if va is None or vb is None:
                return None
            return float(vb) - float(va)
        except Exception as e:
            print("[WARN] MemoryTracker.delta failed: {}".format(e))
            return None

    def report(self):
        lines = [
            "MemoryTracker Report",
            "label,elapsed_s,ws_mb,priv_mb,delta_priv_mb",
        ]
        prev = None
        for rec in self._records:
            priv = rec.get("priv_mb")
            delta = None
            if prev is not None and priv is not None and prev.get("priv_mb") is not None:
                delta = float(priv) - float(prev.get("priv_mb"))
            warn = ""
            if delta is not None and delta > 50.0:
                warn = "[!!] "
            lines.append(
                "{}{}, {:.3f}, {}, {}, {}".format(
                    warn,
                    rec.get("label", ""),
                    float(rec.get("elapsed_s") or 0.0),
                    "" if rec.get("ws_mb") is None else "{:.2f}".format(float(rec.get("ws_mb"))),
                    "" if priv is None else "{:.2f}".format(float(priv)),
                    "" if delta is None else "{:+.2f}".format(float(delta)),
                )
            )
            prev = rec
        return "\n".join(lines)

    def export_csv(self, path):
        try:
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["label", "elapsed_s", "wall_time", "ws_mb", "priv_mb"])
                w.writeheader()
                for r in self._records:
                    w.writerow(r)
        except Exception as e:
            print("[WARN] MemoryTracker.export_csv failed: {}".format(e))
        return path

    def to_dict(self):
        return list(self._records)
