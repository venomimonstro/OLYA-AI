from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.schemas.chat import AnswerRequirement
from app.services.quality import DeterministicAudit


_HIGH_RISK = re.compile(
    r"\b(?:аудит|проверь|проверить|безопасност|уязвим|архитектур|миграц|production|продакшн|"
    r"код|программ|договор|юрид|закон|налог|финанс|расч[её]т|экономик|инвестиц|медицин|диагноз|"
    r"сравни|исследован|источник|документ|регламент|security|audit|architecture|migration|code|"
    r"legal|financial|medical|research|compare|verify)\b",
    re.IGNORECASE,
)
_LOW_RISK_TRANSFORM = re.compile(
    r"^(?:исправь\s+(?:опечатки|орфографию|пунктуацию)|перефразируй|сократи|переведи|"
    r"fix\s+(?:typos|grammar)|rewrite|translate|shorten)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VerificationPlan:
    mode: str
    risk_score: int
    reasons: tuple[str, ...]
    run_critic: bool
    repair_deterministic: bool
    repair_critic: bool
    critic_max_tokens: int = 700

    @property
    def extra_inference_budget(self) -> int:
        # One possible deterministic repair plus one critic plus one possible
        # critic-driven repair are never all needed in Auto. The chat pipeline
        # enforces an overall two-extra-call cap after the primary generation.
        if self.mode == "off":
            return 0
        if self.mode == "strict":
            return 2
        if self.repair_deterministic or self.run_critic:
            return 2
        return 0


def _failed_keys(audit: DeterministicAudit | None) -> set[str]:
    if audit is None:
        return set()
    return {str(item.get("key", "")) for item in audit.checks if item.get("status") == "failed"}


def plan_verification(
    *,
    verification: str,
    user_text: str,
    route_mode: str,
    requirements: Iterable[AnswerRequirement],
    freshness_required: bool,
    verified_source_count: int,
    answer: str = "",
    deterministic: DeterministicAudit | None = None,
) -> VerificationPlan:
    if verification == "off":
        return VerificationPlan("off", 0, (), False, False, False)

    reasons: list[str] = []
    score = 0
    requirement_count = sum(1 for _ in requirements)
    failed = _failed_keys(deterministic)

    if route_mode == "deep":
        score += 2
        reasons.append("deep_reasoning")
    elif route_mode == "work":
        score += 1

    if requirement_count:
        score += 1
        reasons.append("explicit_requirements")
    if requirement_count >= 3:
        score += 1

    if freshness_required:
        score += 2
        reasons.append("freshness_sensitive")
        if verified_source_count < 1:
            score += 2
            reasons.append("fresh_evidence_missing")

    if _HIGH_RISK.search(user_text):
        score += 2
        reasons.append("semantic_high_risk")

    if len(answer) >= 5000:
        score += 1
        reasons.append("long_answer")

    if deterministic is not None and deterministic.unverifiable:
        score += 2
        reasons.append("deterministic_unverified")

    if failed:
        score += 2
        reasons.append("deterministic_failure")

    low_risk_transform = bool(_LOW_RISK_TRANSFORM.search(user_text.strip())) and not freshness_required
    if low_risk_transform and not requirement_count and deterministic is not None and not deterministic.failed:
        score = max(0, score - 2)
        reasons.append("low_risk_transform")

    if verification == "strict":
        return VerificationPlan(
            mode="strict",
            risk_score=max(score, 3),
            reasons=tuple(dict.fromkeys(reasons + ["strict_requested"])),
            run_critic=True,
            repair_deterministic=bool(failed),
            repair_critic=True,
            critic_max_tokens=800,
        )

    # Auto: deterministic violations are concrete defects and may be repaired
    # without paying for a critic first. Semantic critic is reserved for elevated
    # risk after deterministic gates are clean.
    repair_deterministic = bool(failed)
    run_critic = not repair_deterministic and score >= 3
    return VerificationPlan(
        mode="auto",
        risk_score=score,
        reasons=tuple(dict.fromkeys(reasons)),
        run_critic=run_critic,
        repair_deterministic=repair_deterministic,
        repair_critic=run_critic,
        critic_max_tokens=700,
    )


def critic_has_repairable_issue(critic: dict | None) -> bool:
    if not critic or not critic.get("ok"):
        return False
    return any(
        isinstance(item, dict) and item.get("severity") in {"critical", "major"}
        for item in critic.get("issues", [])
    )


def audit_with_critic_issues(audit: DeterministicAudit, critic: dict | None) -> DeterministicAudit:
    """Convert critic major/critical findings into explicit repair targets.

    This keeps AnswerQualityEngine.repair_messages as the single repair prompt
    builder and avoids teaching the chat route a second prompt format.
    """
    if not critic_has_repairable_issue(critic):
        return audit
    checks = list(audit.checks)
    for index, issue in enumerate(critic.get("issues", [])[:10], start=1):
        if not isinstance(issue, dict) or issue.get("severity") not in {"critical", "major"}:
            continue
        checks.append(
            {
                "key": f"critic_issue_{index}",
                "label": "Семантическая проверка нашла дефект",
                "status": "failed",
                "detail": str(issue.get("message", ""))[:1000],
            }
        )
    return DeterministicAudit(checks=checks, warnings=list(audit.warnings))
