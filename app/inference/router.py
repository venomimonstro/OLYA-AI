from dataclasses import dataclass
from typing import Literal

Mode = Literal["fast", "work", "deep"]


@dataclass(frozen=True)
class RouteDecision:
    mode: Mode
    max_context_tokens: int
    max_output_tokens: int
    reasoning: bool
    complexity_score: int = 0
    reason: str = "default"


HIGH_RISK_MARKERS = (
    "аудит безопасности",
    "security audit",
    "найди уязвим",
    "find vulnerab",
    "проведи исследование",
    "deep research",
    "проанализируй репозитор",
    "audit the repository",
    "архитектурный аудит",
    "seo аудит",
    "seo-аудит",
    "seo audit",
    "аудит сайта",
    "site audit",
    "production incident",
    "расследуй инцидент",
    "юридический анализ",
    "medical diagnosis",
)

ANALYTIC_MARKERS = (
    "проанализируй", "сравни", "разработай стратег", "спроектируй", "архитектур",
    "оптимизируй", "найди причину", "найди лучш", "подбери лучш", "посоветуй лучш",
    "рекомендуй лучш", "debug", "root cause", "analyze", "compare", "design", "strategy",
    "план реализации",
)

CODE_MARKERS = (
    "код", "функци", "класс", "api", "sql", "docker", "fastapi", "python", "javascript",
    "typescript", "repository", "репозитор", "тест", "bug", "баг",
)

LIGHT_TASK_MARKERS = (
    "перепиши", "rephrase", "сократи", "shorten", "исправь орфограф", "proofread",
    "переведи", "translate", "кратко", "briefly",
)


def _complexity_score(normalized: str) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    size = len(normalized)
    if size > 8_000:
        score += 4; reasons.append("very_long_input")
    elif size > 4_000:
        score += 2; reasons.append("long_input")
    elif size > 1_800:
        score += 1; reasons.append("medium_input")

    analytic_hits = sum(marker in normalized for marker in ANALYTIC_MARKERS)
    if analytic_hits:
        score += min(4, analytic_hits * 2); reasons.append("analysis_task")
    if any(marker in normalized for marker in ("найди лучш", "подбери лучш", "посоветуй лучш", "рекомендуй лучш")):
        score += 1; reasons.append("comparative_recommendation")

    code_hits = sum(marker in normalized for marker in CODE_MARKERS)
    if code_hits >= 2:
        score += 2; reasons.append("code_task")
    elif code_hits == 1:
        score += 1; reasons.append("code_signal")

    structural_signals = normalized.count("\n-") + normalized.count("\n1.") + normalized.count("\n2.")
    if structural_signals >= 3:
        score += 2; reasons.append("multi_deliverable")
    if any(marker in normalized for marker in LIGHT_TASK_MARKERS):
        score -= 2 if size < 4_000 else 1; reasons.append("light_transformation_task")
    return max(-2, min(10, score)), reasons


def choose_route(text: str, requested_mode: str, normal_context: int, deep_context: int) -> RouteDecision:
    """Map UI quality levels to real inference profiles.

    Public UX names are Simple / Medium / High. Internal values remain
    fast / work / deep for backwards compatibility with persisted usage data.
    Auto remains an API-compatible adaptive mode and is not required in the UI.
    """
    normalized = text.casefold().strip()
    deep_limit = max(1024, int(deep_context))
    normal_limit = min(max(1024, int(normal_context)), deep_limit)
    starter_4k = deep_limit <= 4096
    score, reasons = _complexity_score(normalized)
    high_risk = next((marker for marker in HIGH_RISK_MARKERS if marker in normalized), None)
    if high_risk:
        score = max(score, 7); reasons.append("high_risk_or_audit")

    if requested_mode == "fast":
        mode: Mode = "fast"; reasons.insert(0, "user_selected_simple")
    elif requested_mode == "work":
        mode = "work"; reasons.insert(0, "user_selected_medium")
    elif requested_mode == "deep":
        mode = "deep"; reasons.insert(0, "user_selected_high")
    elif high_risk or score >= 6:
        mode = "deep"; reasons.insert(0, "auto_high")
    elif score >= 2:
        mode = "work"; reasons.insert(0, "auto_medium")
    else:
        mode = "fast"; reasons.insert(0, "auto_simple")

    if mode == "fast":
        # Super-fast lane: no hidden thinking pass. Still enough output budget
        # for a useful answer rather than the old artificially terse Fast lane.
        return RouteDecision(
            mode="fast",
            max_context_tokens=normal_limit,
            max_output_tokens=620 if starter_4k else 1100,
            reasoning=False,
            complexity_score=score,
            reason=",".join(reasons),
        )

    if mode == "deep":
        # High quality uses internal thinking and the full configured context.
        # On a 4K runtime keep output below half the context so the prompt and
        # retrieved evidence are not squeezed out by a huge completion reserve.
        return RouteDecision(
            mode="deep",
            max_context_tokens=deep_limit,
            max_output_tokens=1400 if starter_4k else 3000,
            reasoning=True,
            complexity_score=score,
            reason=",".join(reasons),
        )

    # Medium is the balanced default: larger answers, with internal reasoning
    # only when the task is genuinely analytical or multi-step.
    work_reasoning = score >= 2
    return RouteDecision(
        mode="work",
        max_context_tokens=normal_limit,
        max_output_tokens=1050 if starter_4k else 2100,
        reasoning=work_reasoning,
        complexity_score=score,
        reason=",".join(reasons),
    )
