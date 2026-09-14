from __future__ import annotations

import re


_HIGH_RISK = re.compile(
    r"\b(?:аудит\s+безопасност|уязвим|архитектурн\w*\s+аудит|production\s+incident|"
    r"проанализируй\s+репозитор|юридическ\w*\s+анализ|медицинск\w*\s+(?:диагноз|анализ)|"
    r"финансов\w*\s+(?:модель|расч[её]т|анализ)|налогов\w*\s+расч[её]т|"
    r"deep\s+research|проведи\s+исследование|security\s+audit|architecture\s+audit|"
    r"audit\s+the\s+repository|legal\s+analysis|medical\s+analysis|financial\s+analysis)\b",
    re.IGNORECASE,
)


def install_high_risk_verification_patch() -> None:
    """Guarantee exactly one critic pass for genuinely high-risk Auto tasks.

    Normal interactive chat stays single-pass. Strict mode retains the original
    verification semantics. Auto high-risk work gets one critic, never the old
    critic+repair cascade, unless a deterministic hard failure already requires
    repair. This keeps the quality gate while bounding CPU latency.
    """
    from app.services import conditional_verification as cv

    current = cv.plan_verification
    if getattr(current, "_olya_high_risk_gate", False):
        return

    def plan_verification(
        *, verification: str, user_text: str, route_mode: str, requirements,
        freshness_required: bool, verified_source_count: int, answer: str = "",
        deterministic=None,
    ):
        plan = current(
            verification=verification,
            user_text=user_text,
            route_mode=route_mode,
            requirements=requirements,
            freshness_required=freshness_required,
            verified_source_count=verified_source_count,
            answer=answer,
            deterministic=deterministic,
        )
        if verification in {"off", "strict"} or not _HIGH_RISK.search(str(user_text or "")):
            return plan
        if plan.repair_deterministic:
            return plan
        reasons = tuple(dict.fromkeys((*plan.reasons, "high_risk_quality_gate")))
        return cv.VerificationPlan(
            mode=plan.mode,
            risk_score=max(4, int(plan.risk_score)),
            reasons=reasons,
            run_critic=True,
            repair_deterministic=False,
            repair_critic=False,
            critic_max_tokens=min(360, max(220, int(plan.critic_max_tokens or 0))),
        )

    plan_verification._olya_high_risk_gate = True  # type: ignore[attr-defined]
    cv.plan_verification = plan_verification
