# vop_interwoven/revit/safe_api.py

from typing import Any, Callable, Dict, Optional, TypeVar

T = TypeVar("T")


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

        if diag is not None:
            try:
                diag.error(
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
            except Exception:
                # Diagnostics must never crash the pipeline
                pass

        if policy == "raise":
            raise

        return default
