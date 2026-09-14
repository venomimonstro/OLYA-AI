from __future__ import annotations

import re

from app.services.atomic_fact_latency_patch import is_stable_atomic_fact

_HIGH_RISK = re.compile(
    r"\b(?:аудит\s+безопасност|уязвим|security\s+audit|production\s+incident|"
    r"архитектурн\w*\s+аудит|проанализируй\s+репозитор|audit\s+the\s+repository|"
    r"юридическ\w*\s+анализ|медицинск\w*\s+(?:диагноз|анализ)|"
    r"финансов\w*\s+(?:модель|расч[её]т|анализ)|налогов\w*\s+расч[её]т|"
    r"deep\s+research|проведи\s+исследование)\b",
    re.IGNORECASE,
)


def _hard_failure(deterministic) -> bool:
    if deterministic is None:
        return False
    for item in getattr(deterministic, "checks", ()):
        if item.get("status") != "failed":
            continue
        # These are genuine output-contract failures worth repairing. Evidence
        # warnings alone must never trigger another full local-model pass for a
        # normal chat question.
        key = str(item.get("key") or "")
        if key == "non_empty" or key == "no_placeholders" or key.startswith("requirement_") or key.startswith("scope_"):
            return True
    return False


def _simple_interactive(text: str, requirements) -> bool:
    clean = " ".join(str(text or "").split())
    if not clean or len(clean) > 800 or list(requirements):
        return False
    if _HIGH_RISK.search(clean):
        return False
    return True


def install_interactive_verification_policy_patch() -> None:
    """Avoid critic/repair cascades for normal interactive chat.

    On a CPU-only node a critic plus repair can triple latency and can even
    reintroduce hallucinations after the evidence pipeline produced a correct
    answer. Normal/atomic questions therefore use one primary inference at most;
    server-side evidence and deterministic checks own factual correctness.
    Complex explicitly high-risk work retains the original conditional verifier.
    """
    from app.services import conditional_verification as cv

    current = cv.plan_verification
    if getattr(current, "_olya_interactive_single_pass", False):
        return

    def plan_verification(
        *, verification: str, user_text: str, route_mode: str, requirements,
        freshness_required: bool, verified_source_count: int, answer: str = "",
        deterministic=None,
    ):
        reqs = list(requirements)
        plan = current(
            verification=verification,
            user_text=user_text,
            route_mode=route_mode,
            requirements=reqs,
            freshness_required=freshness_required,
            verified_source_count=verified_source_count,
            answer=answer,
            deterministic=deterministic,
        )
        if verification == "strict":
            return plan
        if _hard_failure(deterministic):
            return plan

        atomic = is_stable_atomic_fact(user_text)
        simple = _simple_interactive(user_text, reqs)
        if not (atomic or simple):
            return plan

        reasons = tuple(dict.fromkeys((*plan.reasons, "interactive_single_pass")))
        return cv.VerificationPlan(
            mode=plan.mode,
            risk_score=min(int(plan.risk_score), 2),
            reasons=reasons,
            run_critic=False,
            repair_deterministic=False,
            repair_critic=False,
            critic_max_tokens=0,
        )

    plan_verification._olya_interactive_single_pass = True  # type: ignore[attr-defined]
    cv.plan_verification = plan_verification
