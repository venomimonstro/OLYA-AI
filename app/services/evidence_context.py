from __future__ import annotations

from contextvars import ContextVar


_EVIDENCE_CONTEXT: ContextVar[str] = ContextVar("x1_quality_evidence_context", default="")


def set_evidence_context(value: str) -> None:
    # ContextVar is request/task-local under asyncio. ContextCompiler overwrites it
    # for every compiled turn, so evidence from one request cannot leak to another.
    _EVIDENCE_CONTEXT.set(str(value or ""))


def current_evidence_context() -> str:
    return _EVIDENCE_CONTEXT.get()
