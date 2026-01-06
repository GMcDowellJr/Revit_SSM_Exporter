# vop_interwoven/core/diagnostics.py

def _exc_to_str(e):
    try:
        return str(e)
    except Exception:
        return "<unstringifiable exception>"


class Diagnostics(object):
    """
    Structured diagnostics recorder (Dynamo-safe minimal stdlib).

    - Bounded event storage
    - Aggregated counts
    - JSON-safe output
    - No dependency on dataclasses / traceback / __future__
    """

    def __init__(self, max_events=200, capture_traceback=False):
        # capture_traceback is accepted for API stability but is a no-op in Dynamo-safe mode.
        self.max_events = max_events
        self.capture_traceback = bool(capture_traceback)

        self.events = []
        self.counts = {}
        self.dropped_events = 0

        # De-duplication state (key -> {index: int|None, suppressed: int})
        # Used to suppress per-element spam while still recording at least one event.
        self._dedupe = {}


    def _count_key(self, level, phase, callsite, exc_type):
        return "{}|{}|{}|{}".format(level, phase, callsite, exc_type or "")


    def _record(self, payload):
        key = self._count_key(
            payload.get("level"),
            payload.get("phase"),
            payload.get("callsite"),
            payload.get("exc_type"),
        )
        self.counts[key] = self.counts.get(key, 0) + 1

        if len(self.events) >= self.max_events:
            self.dropped_events += 1
            return None

        # Note: No traceback capture (stdlib not guaranteed in Dynamo CPython host)
        self.events.append(payload)
        return len(self.events) - 1

    def debug(
        self,
        phase,
        callsite,
        message,
        view_id=None,
        elem_id=None,
        source=None,
        doc_key=None,
        extra=None,
    ):
        payload = {
            "level": "DEBUG",
            "phase": phase,
            "callsite": callsite,
            "message": message,
            "exc_type": None,
            "exc_message": None,
            "view_id": view_id,
            "elem_id": elem_id,
            "source": source,
            "doc_key": doc_key,
            "extra": extra or {},
        }
        self._record(payload)


    def info(
        self,
        phase,
        callsite,
        message,
        view_id=None,
        elem_id=None,
        source=None,
        doc_key=None,
        extra=None,
    ):
        payload = {
            "level": "INFO",
            "phase": phase,
            "callsite": callsite,
            "message": message,
            "exc_type": None,
            "exc_message": None,
            "view_id": view_id,
            "elem_id": elem_id,
            "source": source,
            "doc_key": doc_key,
            "extra": extra or {},
        }
        self._record(payload)


    def debug_dedupe(
        self,
        dedupe_key,
        phase,
        callsite,
        message,
        view_id=None,
        elem_id=None,
        source=None,
        doc_key=None,
        extra=None,
    ):
        """Record at most one DEBUG event per dedupe_key, with a suppressed_count.

        - First call records a DEBUG event with extra.suppressed_count=0.
        - Subsequent calls increment suppressed_count without recording more events.

        This is intended for "optimization disabled; continuing" paths.
        """
        entry = self._dedupe.get(dedupe_key)
        if entry is None:
            payload_extra = dict(extra or {})
            payload_extra.setdefault("suppressed_count", 0)
            payload = {
                "level": "DEBUG",
                "phase": phase,
                "callsite": callsite,
                "message": message,
                "exc_type": None,
                "exc_message": None,
                "view_id": view_id,
                "elem_id": elem_id,
                "source": source,
                "doc_key": doc_key,
                "extra": payload_extra,
            }
            idx = self._record(payload)
            self._dedupe[dedupe_key] = {"index": idx, "suppressed": 0}
            return

        # Already recorded once; suppress and update first event if possible.
        entry["suppressed"] += 1
        idx = entry.get("index")
        if idx is not None and 0 <= idx < len(self.events):
            try:
                ev = self.events[idx]
                ev_extra = ev.get("extra")
                if isinstance(ev_extra, dict):
                    ev_extra["suppressed_count"] = entry["suppressed"]
            except Exception:
                # Diagnostics must never throw.
                pass

    def warn(
        self,
        phase,
        callsite,
        message,
        view_id=None,
        elem_id=None,
        source=None,
        doc_key=None,
        extra=None,
    ):
        payload = {
            "level": "WARN",
            "phase": phase,
            "callsite": callsite,
            "message": message,
            "exc_type": None,
            "exc_message": None,
            "view_id": view_id,
            "elem_id": elem_id,
            "source": source,
            "doc_key": doc_key,
            "extra": extra or {},
        }
        self._record(payload)

    def error(
        self,
        phase,
        callsite,
        message,
        exc=None,
        view_id=None,
        elem_id=None,
        source=None,
        doc_key=None,
        extra=None,
    ):
        payload = {
            "level": "ERROR",
            "phase": phase,
            "callsite": callsite,
            "message": message,
            "exc_type": type(exc).__name__ if exc is not None else None,
            "exc_message": _exc_to_str(exc) if exc is not None else None,
            "view_id": view_id,
            "elem_id": elem_id,
            "source": source,
            "doc_key": doc_key,
            "extra": extra or {},
        }
        self._record(payload)

    def to_dict(self):
        return {
            "max_events": self.max_events,
            "num_events": len(self.events),
            "dropped_events": self.dropped_events,
            "counts": dict(self.counts),
            "events": list(self.events),
        }
