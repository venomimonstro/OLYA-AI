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

    if requested_mode in {"fast", "work", "deep"}:
        mode: Mode = requested_mode  # type: ignore[assignment]
    elif any(marker in normalized for marker in HIGH_RISK_MARKERS) or len(normalized) > 8_000:
        mode = "deep"
    elif any(marker in normalized for marker in FAST_MARKERS) and len(normalized) < 4_000:
        mode = "fast"
    else:
        mode = "work"

    if mode == "fast":
        return RouteDecision(mode, min(normal_context, 4096), 700, False)
    if mode == "deep":
        return RouteDecision(mode, deep_context, 2200, True)
    return RouteDecision(mode, normal_context, 1200, False)
