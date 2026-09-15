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
    "проанализируй", "анализ", "сравни", "разработай стратег", "спроектируй", "архитектур",
    "оптимизируй", "найди причину", "найди лучш", "подбери лучш", "посоветуй лучш",
    "рекомендуй", "что лучше", "что выбрать", "как лучше", "почему", "debug", "root cause",
    "analyze", "compare", "design", "strategy", "план реализации", "варианты", "плюсы и минусы",
)

CODE_MARKERS = (
    "код", "функци", "класс", "api", "sql", "docker", "fastapi", "python", "javascript",
    "typescript", "repository", "репозитор", "тест", "bug", "баг",
)

LIGHT_TASK_MARKERS = (
    "перепиши", "rephrase", "сократи", "shorten", "исправь орфограф", "proofread",
    "переведи", "translate", "кратко", "briefly",
)

_SIMPLE_FACT_RE = re.compile(
    r"^\s*(?:кто|что|где|когда|сколько|какой|какая|какое|как\s+называется|"
    r"who|what|where|when|how\s+much)\b",
    re.I,
)

_EXPLICIT_REASONING_RE = re.compile(
    r"(?:подумай|разберись|рассуди|обоснуй|докажи|проверь\s+логику|найди\s+ошиб|"
    r"детальн\w*\s+анализ|глубок\w*\s+анализ|многорол|комплексн\w*\s+аудит|"
    r"think|reason|prove|deep\s+analysis|thorough\s+analysis)",
    re.I,
)

_LONG_FORM_RE = re.compile(
    r"(?:"
    r"\b\d{4,5}\s*(?:символ\w*|знак\w*|character\w*)\b|"
    r"\bне\s+менее\s+\d{4,5}\s*(?:символ\w*|знак\w*)\b|"
    r"\b(?:больш\w*|длинн\w*|подробн\w*|разв[её]рнут\w*)\s+"
    r"(?:(?:больш\w*|длинн\w*|подробн\w*|разв[её]рнут\w*)\s+)?стать\w*\b|"
    r"\bлонгрид\w*\b|\blong[- ]?form\b|\blongread\b|"
    r"\b(?:3000|4000|5000)\s+words?\b"
    r")",
    re.IGNORECASE,
)


def _complexity_score(normalized: str) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    size = len(normalized)
    if size > 8_000:
        score += 4; reasons.append("very_long_input")
    elif size > 4_000:
        score += 3; reasons.append("long_input")
    elif size > 1_800:
        score += 2; reasons.append("medium_input")
    elif size > 550:
        score += 1; reasons.append("nontrivial_input")

    analytic_hits = sum(marker in normalized for marker in ANALYTIC_MARKERS)
    if analytic_hits:
        score += min(5, analytic_hits * 2); reasons.append("analysis_task")

    if _EXPLICIT_REASONING_RE.search(normalized):
        score += 4; reasons.append("explicit_reasoning")

    code_hits = sum(marker in normalized for marker in CODE_MARKERS)
    if code_hits >= 2:
        score += 3; reasons.append("code_task")
    elif code_hits == 1:
        score += 1; reasons.append("code_signal")

    structural_signals = (
        normalized.count("\n-") + normalized.count("\n*") +
        normalized.count("\n1.") + normalized.count("\n2.") + normalized.count(";")
    )
    if structural_signals >= 5:
        score += 3; reasons.append("multi_deliverable")
    elif structural_signals >= 2:
        score += 1; reasons.append("structured_request")

    if "и ещё" in normalized or "дополнительно" in normalized or "отдельно" in normalized:
        score += 1; reasons.append("multi_part")

    if any(marker in normalized for marker in LIGHT_TASK_MARKERS):
        score -= 3 if size < 2_000 else 1; reasons.append("light_transformation_task")
    return max(-3, min(12, score)), reasons


def choose_route(text: str, requested_mode: str, normal_context: int, deep_context: int) -> RouteDecision:
    """Quality-first adaptive routing.

    Fast is reserved for truly atomic/light requests. Normal user questions use
    work mode by default; multi-step analysis/recommendation/audits automatically
    receive deep reasoning. Explicit user mode always wins.
    """
    normalized = text.casefold().strip()
    deep_limit = max(1024, int(deep_context))
    normal_limit = min(max(1024, int(normal_context)), deep_limit)
    starter_4k = deep_limit <= 4096
    score, reasons = _complexity_score(normalized)
    high_risk = next((marker for marker in HIGH_RISK_MARKERS if marker in normalized), None)
    long_form = bool(_LONG_FORM_RE.search(normalized))
    atomic_cap = atomic_output_cap(text) if requested_mode in {"auto", "fast"} else None
    light_task = any(marker in normalized for marker in LIGHT_TASK_MARKERS) and len(normalized) < 2500
    simple_fact = bool(_SIMPLE_FACT_RE.search(normalized)) and len(normalized) <= 120 and score <= 0

    if high_risk:
        score = max(score, 8); reasons.append("high_risk_or_audit")
    if long_form:
        score = max(score, 4); reasons.append("explicit_long_form")
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
    elif atomic_cap is not None or light_task or simple_fact:
        mode = "fast"; reasons.insert(0, "auto_simple")
    else:
        # Quality-first default: ordinary questions deserve a full work pass,
        # not the latency-optimized atomic profile.
        mode = "work"; reasons.insert(0, "auto_medium_default")

    if long_form and starter_4k:
        return RouteDecision(
            mode="work" if mode == "fast" else mode,
            max_context_tokens=deep_limit,
            max_output_tokens=3000,
            reasoning=mode == "deep",
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
            max_output_tokens=700 if starter_4k else 1200,
            reasoning=False,
            complexity_score=score,
            reason=",".join(reasons),
        )

    if mode == "deep":
        return RouteDecision(
            mode="deep",
            max_context_tokens=deep_limit,
            max_output_tokens=1900 if starter_4k else 3600,
            reasoning=True,
            complexity_score=score,
            reason=",".join(reasons),
        )

    # Medium mode now reasons for any genuine analysis/comparison task; plain
    # explanatory questions remain cheaper while still receiving a full answer.
    work_reasoning = score >= 2
    return RouteDecision(
        mode="work",
        max_context_tokens=normal_limit,
        max_output_tokens=1350 if starter_4k else 2600,
        reasoning=work_reasoning,
        complexity_score=score,
        reason=",".join(reasons),
    )
