from __future__ import annotations

from contextvars import ContextVar


_SOURCE_EVIDENCE: ContextVar[str] = ContextVar("x1_source_evidence_context", default="")


def set_evidence_context(value: str) -> None:
    """Set server-owned source evidence for the current async request/task.

    Callers must only pass evidence created by trusted X1 services such as
    SourceContextBuilder. User-authored text must never be promoted through this
    function merely because it resembles an internal source marker.
    """
    _SOURCE_EVIDENCE.set(str(value or ""))


def current_evidence_context() -> str:
    return _SOURCE_EVIDENCE.get()
