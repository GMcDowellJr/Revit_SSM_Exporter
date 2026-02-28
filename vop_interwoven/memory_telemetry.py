"""Memory telemetry helpers for Dynamo/Python.NET runtime."""

from datetime import datetime
import csv
import time


def _get_memory_mb():
    """Return current (working_set_mb, private_bytes_mb) or (None, None)."""
    try:
        import System
        proc = System.Diagnostics.Process.GetCurrentProcess()
        proc.Refresh()
        mb = float(1024.0 * 1024.0)
        return (float(proc.WorkingSet64) / mb, float(proc.PrivateMemorySize64) / mb)
    except Exception as e:
        print("[memory_telemetry] _get_memory_mb failed: {}".format(e))
        return (None, None)


def _force_clr_gc():
    """Run CLR GC triple-call pattern and report private-memory delta."""
    try:
        import System
        _ws0, priv0 = _get_memory_mb()
        System.GC.Collect()
        System.GC.WaitForPendingFinalizers()
        System.GC.Collect()
        _ws1, priv1 = _get_memory_mb()
        freed = None
        if priv0 is not None and priv1 is not None:
            freed = float(priv0) - float(priv1)
        return {"pre_priv_mb": priv0, "post_priv_mb": priv1, "freed_mb": freed}
    except Exception as e:
        print("[memory_telemetry] _force_clr_gc failed: {}".format(e))
        return {"pre_priv_mb": None, "post_priv_mb": None, "freed_mb": None}


class MemoryTracker(object):
    def __init__(self):
        self._t0 = time.perf_counter()
        self._records = []
        self.gc_events = []

    def mark(self, label):
        try:
            ws_mb, priv_mb = _get_memory_mb()
            rec = {
                "label": str(label),
                "elapsed_s": float(time.perf_counter() - self._t0),
                "wall_time": datetime.now().isoformat(),
                "ws_mb": ws_mb,
                "priv_mb": priv_mb,
            }
            self._records.append(rec)
            return rec
        except Exception as e:
            print("[memory_telemetry] mark failed: {}".format(e))
            rec = {
                "label": str(label),
                "elapsed_s": float(time.perf_counter() - self._t0),
                "wall_time": datetime.now().isoformat(),
                "ws_mb": None,
                "priv_mb": None,
            }
            self._records.append(rec)
            return rec

    def mark_and_gc(self, label):
        try:
            gc_info = _force_clr_gc()
            self.gc_events.append(dict(gc_info))
        except Exception as e:
            print("[memory_telemetry] mark_and_gc gc failed: {}".format(e))
        return self.mark("{} [post-GC]".format(label))

    def delta(self, label_a, label_b, field="priv_mb"):
        try:
            a = None
            b = None
            for r in self._records:
                if r.get("label") == label_a:
                    a = r
                if r.get("label") == label_b:
                    b = r
            if a is None or b is None:
                return None
            va = a.get(field)
            vb = b.get(field)
            if va is None or vb is None:
                return None
            return float(vb) - float(va)
        except Exception as e:
            print("[memory_telemetry] delta failed: {}".format(e))
            return None

    def report(self):
        try:
            lines = ["MemoryTracker Report", "label | elapsed_s | ws_mb | priv_mb | delta_priv_mb"]
            prev_priv = None
            for r in self._records:
                priv = r.get("priv_mb")
                delta = None if prev_priv is None or priv is None else (float(priv) - float(prev_priv))
                warn = "[!!] " if (delta is not None and delta > 50.0) else ""
                lines.append(
                    "{}{} | {:.3f} | {} | {} | {}".format(
                        warn,
                        r.get("label", ""),
                        float(r.get("elapsed_s", 0.0)),
                        "" if r.get("ws_mb") is None else "{:.2f}".format(float(r.get("ws_mb"))),
                        "" if priv is None else "{:.2f}".format(float(priv)),
                        "" if delta is None else "{:+.2f}".format(float(delta)),
                    )
                )
                prev_priv = priv
            return "\n".join(lines)
        except Exception as e:
            print("[memory_telemetry] report failed: {}".format(e))
            return "MemoryTracker report unavailable"

    def export_csv(self, path):
        try:
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["label", "elapsed_s", "wall_time", "ws_mb", "priv_mb"])
                w.writeheader()
                for r in self._records:
                    w.writerow(r)
            return path
        except Exception as e:
            print("[memory_telemetry] export_csv failed: {}".format(e))
            return path

    def to_dict(self):
        try:
            return list(self._records)
        except Exception as e:
            print("[memory_telemetry] to_dict failed: {}".format(e))
            return []
