from __future__ import annotations

from app.inference.client import LlamaClient


def install_reasoning_budget_patch() -> None:
    current = LlamaClient._thinking_budget
    if getattr(current, "_olya_cpu_reasoning_budget", False):
        return

    def thinking_budget(max_tokens: int) -> int:
        value = max(64, int(max_tokens))
        if value <= 700:
            return 64
        if value <= 1150:
            return 96
        return 160

    thinking_budget._olya_cpu_reasoning_budget = True  # type: ignore[attr-defined]
    LlamaClient._thinking_budget = staticmethod(thinking_budget)
