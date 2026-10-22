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


# --- C2: one process-memory sample, cheap enough to take per view -------------------
#
# Batch 2026-10-21: per-view work in our own Python grew with the batch's
# position (model_expand +0.2..0.6 s per view from run 1 to run 7) while
# Revit's export time did not. Session state (heap growth, paging) is one
# explanation; a sample at view start and capture start/end is what tells it
# from the other. Read WITHOUT forcing a collection (GetTotalMemory(False)),
# so the sample does not change what it measures.
#
# The OS counters come from K32GetProcessMemoryInfo first: one call on a
# pseudo-handle. System.Diagnostics.Process.Refresh() is the fallback only:
# on Windows it snapshots EVERY process (NtQuerySystemInformation), which is
# not a per-view cost to pay when the cheap call works.
PROCESS_MEMORY_SCHEMA = "vop.process_memory.v1"
_perf = getattr(time, "perf_counter", time.time)
_PSAPI = []     # (GetCurrentProcess, K32GetProcessMemoryInfo, struct), built once


def _psapi_counters():
    """``(working_set, private_bytes, page_faults, "psapi")``. Raises off Windows."""
    import ctypes
    if not _PSAPI:
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            # PROCESS_MEMORY_COUNTERS_EX
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t)]
        kernel32 = ctypes.windll.kernel32
        current = kernel32.GetCurrentProcess
        current.restype = wintypes.HANDLE
        info = kernel32.K32GetProcessMemoryInfo
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        info.restype = wintypes.BOOL
        _PSAPI.append((current, info, _Counters))
    current, info, counters_type = _PSAPI[0]
    counters = counters_type()
    counters.cb = ctypes.sizeof(counters)
    if not info(current(), ctypes.byref(counters), counters.cb):
        raise OSError("K32GetProcessMemoryInfo failed: {0}".format(ctypes.GetLastError()))
    return (int(counters.WorkingSetSize), int(counters.PrivateUsage),
            int(counters.PageFaultCount), "psapi")


def _dotnet_process_counters():
    """``(working_set, private_bytes, None, "System.Diagnostics.Process")``."""
    import System
    proc = System.Diagnostics.Process.GetCurrentProcess()
    proc.Refresh()
    return int(proc.WorkingSet64), int(proc.PrivateMemorySize64), None, \
        "System.Diagnostics.Process"


def _managed_counters():
    """``(managed_heap_bytes, [gen0, gen1, gen2])`` -- no collection forced."""
    import System
    return (int(System.GC.GetTotalMemory(False)),
            [int(System.GC.CollectionCount(g)) for g in (0, 1, 2)])


def process_memory_sample():
    """One sample: working set, private bytes, page faults (cumulative), the
    managed heap and the CLR collection counts, plus ``read_ms`` -- what the
    sample itself cost. A value that cannot be read is None and ``errors``
    names why; the sample never raises."""
    t0 = _perf()
    sample = {"schema": PROCESS_MEMORY_SCHEMA, "working_set_bytes": None,
              "private_bytes": None, "page_faults": None, "os_source": None,
              "managed_heap_bytes": None, "gc_collections": None, "errors": {}}
    for name, reader in (("psapi_counters", _psapi_counters),
                         ("dotnet_process_counters", _dotnet_process_counters)):
        try:
            (sample["working_set_bytes"], sample["private_bytes"],
             sample["page_faults"], sample["os_source"]) = reader()
            break
        except Exception as ex:  # noqa: BLE001 -- the next reader, and recorded
            sample["errors"][name] = "{0}: {1}".format(type(ex).__name__, ex)
    try:
        sample["managed_heap_bytes"], sample["gc_collections"] = _managed_counters()
    except Exception as ex:  # noqa: BLE001 -- recorded
        sample["errors"]["managed_counters"] = "{0}: {1}".format(type(ex).__name__, ex)
    if not sample["errors"]:
        del sample["errors"]
    sample["read_ms"] = round((_perf() - t0) * 1000.0, 3)
    return sample
