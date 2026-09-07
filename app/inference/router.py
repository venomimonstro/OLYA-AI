from dataclasses import dataclass
from typing import Literal

Mode = Literal["fast", "work", "deep"]


@dataclass(frozen=True)
class RouteDecision:
    mode: Mode
    max_context_tokens: int
    max_output_tokens: int
    reasoning: bool


HIGH_RISK_MARKERS = (
    "аудит безопасности",
    "архитектур",
    "исправь проект",
    "проведи исследование",
    "найди уязвим",
    "проанализируй репозитор",
)

FAST_MARKERS = (
    "перепиши",
    "сократи",
    "исправь орфограф",
    "переведи",
    "кратко",
)


def choose_route(text: str, requested_mode: str, normal_context: int, deep_context: int) -> RouteDecision:
    normalized = text.lower().strip()

    # llama.cpp is booted with X1_DEEP_CONTEXT_TOKENS as its physical context
    # ceiling. A stale/misconfigured normal-context value must never let Work
    # compile a larger prompt than the running model can accept.
    deep_limit = max(1024, int(deep_context))
    normal_limit = min(max(1024, int(normal_context)), deep_limit)

    if requested_mode in {"fast", "work", "deep"}:
        mode: Mode = requested_mode  # type: ignore[assignment]
    elif any(marker in normalized for marker in HIGH_RISK_MARKERS) or len(normalized) > 8_000:
        mode = "deep"
    elif any(marker in normalized for marker in FAST_MARKERS) and len(normalized) < 4_000:
        mode = "fast"
    else:
        mode = "work"

    if mode == "fast":
        return RouteDecision(mode, min(normal_limit, 4096), 700, False)
    if mode == "deep":
        return RouteDecision(mode, deep_limit, 2200, True)
    return RouteDecision(mode, normal_limit, 1200, False)
