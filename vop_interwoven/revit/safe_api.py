# vop_interwoven/revit/safe_api.py

from typing import Any, Callable, Dict, Optional, TypeVar
import sys

T = TypeVar("T")


def _diag_fallback_stderr(level, phase, callsite, message, exc=None, extra=None):
    """
    CRITICAL FALLBACK: If diagnostic recording fails, print to stderr.

    This ensures error visibility even if the diagnostic system is broken.
    Without this, all error visibility is lost when diagnostics fail.
    """
    try:
        exc_info = ""
        if exc is not None:
            exc_info = " | exc_type={} exc_msg={}".format(
                type(exc).__name__,
                str(exc)[:200]  # Truncate long messages
            )
        extra_info = ""
        if extra:
            try:
                extra_info = " | extra={}".format(extra)
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="general",
                        callsite="_diag_fallback_stderr",
                        message="Exception in _diag_fallback_stderr: {}".format(e),
                        exc=e,
                    )
                extra_info = " | extra=<unstringifiable>"

        fallback_msg = "[DIAG_FALLBACK] {level} | phase={phase} | callsite={callsite} | {message}{exc}{extra}".format(
            level=level,
            phase=phase,
            callsite=callsite,
            message=message[:500],  # Truncate long messages
            exc=exc_info,
            extra=extra_info,
        )
        print(fallback_msg, file=sys.stderr)
    except Exception as fallback_error:
        # Last resort: even stderr formatting failed
        try:
            print("[CRITICAL] Diagnostic fallback failed: {}".format(fallback_error), file=sys.stderr)
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="general",
                    callsite="_diag_fallback_stderr",
                    message="Exception in _diag_fallback_stderr: {}".format(e),
                    exc=e,
                )
def record_error(diag, phase, callsite, message, exc=None, view_id=None, elem_id=None, source=None, doc_key=None, extra=None):
    """
    Record an error with diagnostic fallback to stderr if recording fails.

    Use this instead of direct diag.error() calls to ensure errors are always visible.
    """
    if diag is None:
        # No diagnostics object - use stderr fallback
        _diag_fallback_stderr("ERROR", phase, callsite, message, exc=exc, extra=extra)
        return

    try:
        diag.error(
            phase=phase,
            callsite=callsite,
            message=message,
            exc=exc,
            view_id=view_id,
            elem_id=elem_id,
            source=source,
            doc_key=doc_key,
            extra=extra,
        )
    except Exception as diag_error:
        _diag_fallback_stderr(
            "ERROR",
            phase,
            callsite,
            "Original: {} | DiagFailure: {}".format(message, diag_error),
            exc=exc,
            extra=extra,
        )


def record_warning(diag, phase, callsite, message, view_id=None, elem_id=None, source=None, doc_key=None, extra=None):
    """
    Record a warning with diagnostic fallback to stderr if recording fails.
    """
    if diag is None:
        _diag_fallback_stderr("WARN", phase, callsite, message, extra=extra)
        return

    try:
        diag.warn(
            phase=phase,
            callsite=callsite,
            message=message,
            view_id=view_id,
            elem_id=elem_id,
            source=source,
            doc_key=doc_key,
            extra=extra,
        )
    except Exception as diag_error:
        _diag_fallback_stderr(
            "WARN",
            phase,
            callsite,
            "Original: {} | DiagFailure: {}".format(message, diag_error),
            extra=extra,
        )


def safe_call(
    diag: Any,
    *,
    phase: str,
    callsite: str,
    fn: Callable[[], T],
    default: T,
    context: Optional[Dict[str, Any]] = None,
    policy: str = "default",  # "default" | "raise"
) -> T:
    """
    Execute fn() and handle exceptions in a controlled, observable way.

    policy:
      - "default": record error, return default
      - "raise":   record error, then re-raise
    """
    try:
        return fn()
    except Exception as e:
        ctx = context or {}

        # Use record_error which has stderr fallback if diag fails
        record_error(
            diag,
            phase=phase,
            callsite=callsite,
            message="Exception in safe_call",
            exc=e,
            view_id=ctx.get("view_id"),
            elem_id=ctx.get("elem_id"),
            source=ctx.get("source"),
            doc_key=ctx.get("doc_key"),
            extra=ctx,
        )

        if policy == "raise":
            raise

        return default


def element_id_value(value):
    """Read an ElementId's integer, Revit 2025's 64-bit ``Value`` first.

    ``ElementId.IntegerValue`` is the legacy 32-bit property, deprecated in
    Revit 2025. For an id outside the int32 range its getter does not return a
    usable number, and ``getattr(value, "IntegerValue", None)`` does not
    protect against that -- a default only covers ``AttributeError``, not a
    property that raises. Reading ``Value`` first, with each property access
    in its own ``try``, is correct on Revit 2025 and unchanged on older hosts,
    which do not expose ``Value`` at all and fall through to ``IntegerValue``.

    Returns None when ``value`` is not an ElementId-like object. There is
    deliberately no ``int(value)`` fallback: callers that also accept a plain
    integer should test for one themselves, so this function's answer always
    means "this was an element id, and here is its value".

    UNCONFIRMED: that ``IntegerValue`` raises rather than truncating on a
    large id has not been verified against a real Revit 2025 host. Reading
    ``Value`` first is correct either way -- a truncated id would be just as
    wrong as a raised one -- so this does not depend on which it does.
    """
    for attr in ("Value", "IntegerValue"):
        try:
            inner = getattr(value, attr, None)
        except Exception:
            continue
        if inner is None:
            continue
        try:
            return int(inner)
        except (TypeError, ValueError):
            continue
    return None
