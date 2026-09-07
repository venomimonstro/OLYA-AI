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


# These tasks have a high cost of a plausible-but-wrong answer. They should get
# the Deep lane even when the literal prompt is short.
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
    "production incident",
    "расследуй инцидент",
    "юридический анализ",
    "medical diagnosis",
)

# Medium-complexity work benefits from thinking, but should not automatically
# consume the full Deep budget.
ANALYTIC_MARKERS = (
    "проанализируй",
    "сравни",
    "разработай стратег",
    "спроектируй",
    "архитектур",
    "оптимизируй",
    "найди причину",
    "debug",
    "root cause",
    "analyze",
    "compare",
    "design",
    "strategy",
    "план реализации",
)

CODE_MARKERS = (
    "код",
    "функци",
    "класс",
    "api",
    "sql",
    "docker",
    "fastapi",
    "python",
    "javascript",
    "typescript",
    "repository",
    "репозитор",
    "тест",
    "bug",
    "баг",
)

FAST_MARKERS = (
    "перепиши",
    "rephrase",
    "сократи",
    "shorten",
    "исправь орфограф",
    "proofread",
    "переведи",
    "translate",
    "кратко",
    "briefly",
)


def _complexity_score(normalized: str) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    size = len(normalized)

    if size > 8_000:
        score += 4
        reasons.append("very_long_input")
    elif size > 4_000:
        score += 2
        reasons.append("long_input")
    elif size > 1_800:
        score += 1
        reasons.append("medium_input")

    analytic_hits = sum(marker in normalized for marker in ANALYTIC_MARKERS)
    if analytic_hits:
        score += min(3, analytic_hits * 2)
        reasons.append("analysis_task")

    code_hits = sum(marker in normalized for marker in CODE_MARKERS)
    if code_hits >= 2:
        score += 2
        reasons.append("code_task")
    elif code_hits == 1:
        score += 1
        reasons.append("code_signal")

    # Multiple explicit deliverables usually require planning even when the
    # prompt does not contain one of the marker phrases.
    structural_signals = normalized.count("\n-") + normalized.count("\n1.") + normalized.count("\n2.")
    if structural_signals >= 3:
        score += 2
        reasons.append("multi_deliverable")

    if any(marker in normalized for marker in FAST_MARKERS):
        score -= 3 if size < 4_000 else 1
        reasons.append("transformation_task")

    return max(-3, min(10, score)), reasons


def choose_route(text: str, requested_mode: str, normal_context: int, deep_context: int) -> RouteDecision:
    normalized = text.casefold().strip()

    # llama.cpp is booted with X1_DEEP_CONTEXT_TOKENS as its physical context
    # ceiling. No runtime route is allowed to exceed that boot-time envelope.
    deep_limit = max(1024, int(deep_context))
    normal_limit = min(max(1024, int(normal_context)), deep_limit)

    score, reasons = _complexity_score(normalized)
    high_risk = next((marker for marker in HIGH_RISK_MARKERS if marker in normalized), None)
    if high_risk:
        score = max(score, 7)
        reasons.append("high_risk")

    if requested_mode in {"fast", "work", "deep"}:
        mode: Mode = requested_mode  # type: ignore[assignment]
        reasons.insert(0, "user_selected")
    elif high_risk or score >= 6:
        mode = "deep"
    elif any(marker in normalized for marker in FAST_MARKERS) and score <= 0:
        mode = "fast"
    else:
        mode = "work"

    if mode == "fast":
        return RouteDecision(
            mode="fast",
            max_context_tokens=min(normal_limit, 4096),
            max_output_tokens=700,
            reasoning=False,
            complexity_score=score,
            reason=",".join(reasons) or "fast_default",
        )

    if mode == "deep":
        return RouteDecision(
            mode="deep",
            max_context_tokens=deep_limit,
            max_output_tokens=2200,
            reasoning=True,
            complexity_score=score,
            reason=",".join(reasons) or "deep_default",
        )

    # Work is the normal lane. Thinking is conditional: this directly prevents
    # the common UX failure where a basic question spends tens of seconds in a
    # hidden reasoning trace, while still giving analytical/code work more depth.
    work_reasoning = score >= 3
    return RouteDecision(
        mode="work",
        max_context_tokens=normal_limit,
        max_output_tokens=1400 if work_reasoning else 1200,
        reasoning=work_reasoning,
        complexity_score=score,
        reason=",".join(reasons) or "work_default",
    )
