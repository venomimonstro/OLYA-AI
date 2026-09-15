from dataclasses import dataclass
import re
from typing import Literal

from app.services.response_strategy import atomic_output_cap

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
    "аудит безопасности", "security audit", "найди уязвим", "find vulnerab",
    "проведи исследование", "deep research", "проанализируй репозитор", "audit the repository",
    "архитектурный аудит", "seo аудит", "seo-аудит", "seo audit", "аудит сайта", "site audit",
    "production incident", "расследуй инцидент", "юридический анализ", "medical diagnosis",
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

_LONG_FORM_RE = re.compile(
    r"(?:\b(?:6000|7000|8000|9000|10000|12000|15000|20000)\s*(?:символ|знак|character)|"
    r"\bне\s+менее\s+\d{4,5}\s*(?:символ|знак)|"
    r"\b(?:большая|длинная|подробная|развёрнутая|развернутая)\s+статья\b|"
    r"\bлонгрид\b|\blong[- ]?form\b|\blongread\b|\b(?:3000|4000|5000)\s+words?\b)",
    re.IGNORECASE,
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
    """Choose one inference profile; long-form is opt-in and simple factual questions stay tiny."""
    normalized = text.casefold().strip()
    deep_limit = max(1024, int(deep_context))
    normal_limit = min(max(1024, int(normal_context)), deep_limit)
    starter_4k = deep_limit <= 4096
    score, reasons = _complexity_score(normalized)
    high_risk = next((marker for marker in HIGH_RISK_MARKERS if marker in normalized), None)
    long_form = bool(_LONG_FORM_RE.search(normalized))
    atomic_cap = atomic_output_cap(text) if requested_mode in {"auto", "fast"} else None
    if high_risk:
        score = max(score, 7); reasons.append("high_risk_or_audit")
    if long_form:
        score = max(score, 3); reasons.append("explicit_long_form")
    if atomic_cap is not None:
        reasons.append("atomic_knowledge")

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

    if long_form and starter_4k:
        return RouteDecision(
            mode="work" if mode == "fast" else mode,
            max_context_tokens=deep_limit,
            max_output_tokens=3000,
            reasoning=False,
            complexity_score=score,
            reason=",".join(reasons),
        )

    if atomic_cap is not None and mode == "fast":
        return RouteDecision(
            mode="fast",
            max_context_tokens=min(normal_limit, 2048),
            max_output_tokens=atomic_cap,
            reasoning=False,
            complexity_score=score,
            reason=",".join(reasons),
        )

    if mode == "fast":
        return RouteDecision(
            mode="fast",
            max_context_tokens=normal_limit,
            max_output_tokens=620 if starter_4k else 1100,
            reasoning=False,
            complexity_score=score,
            reason=",".join(reasons),
        )

    if mode == "deep":
        return RouteDecision(
            mode="deep",
            max_context_tokens=deep_limit,
            max_output_tokens=1400 if starter_4k else 3000,
            reasoning=True,
            complexity_score=score,
            reason=",".join(reasons),
        )

    work_reasoning = score >= 4
    return RouteDecision(
        mode="work",
        max_context_tokens=normal_limit,
        max_output_tokens=1050 if starter_4k else 2100,
        reasoning=work_reasoning,
        complexity_score=score,
        reason=",".join(reasons),
    )
