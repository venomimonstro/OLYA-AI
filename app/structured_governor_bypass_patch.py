from __future__ import annotations

from contextlib import asynccontextmanager


def _structured_answer_ready() -> bool:
    try:
        from app.services.interactive_evidence import current_interactive_evidence
        return bool(str(current_interactive_evidence().resolved_answer or "").strip())
    except Exception:
        return False


def install_structured_governor_bypass_patch() -> None:
    """Do not queue server-resolved live facts behind llama.cpp.

    The structured answer guard returns these responses without touching the
    inference backend. Acquiring the single-generation ResourceGovernor first
    would therefore create artificial latency with no safety benefit.
    """
    from app.services.resource_governor import ResourceGovernor

    current = ResourceGovernor.slot
    if getattr(current, "_olya_structured_bypass", False):
        return

    @asynccontextmanager
    async def slot(self, *args, **kwargs):
        if _structured_answer_ready():
            yield
            return
        async with current(self, *args, **kwargs):
            yield

    slot._olya_structured_bypass = True  # type: ignore[attr-defined]
    ResourceGovernor.slot = slot
