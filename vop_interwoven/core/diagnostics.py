# vop_interwoven/core/diagnostics.py
from __future__ import annotations

def warn(ctx: str, msg: str, exc: Exception | None = None) -> None:
    """
    Minimal, dependency-free diagnostics channel.

    Intentionally uses print() because:
    - Dynamo/Revit CPython contexts often lack structured logging
    - stdout is frequently the only available channel
    """
    if exc is None:
        print(f"[WARN] {ctx}: {msg}")
    else:
        print(f"[WARN] {ctx}: {msg} ({type(exc).__name__}: {exc})")
