from pathlib import Path

from app.schemas.chat import AnswerRequirement
from app.services.conditional_verification import (
    audit_with_critic_issues,
    critic_has_repairable_issue,
    plan_verification,
)
from app.services.quality import DeterministicAudit


def clean_audit() -> DeterministicAudit:
    return DeterministicAudit(
        checks=[{"key": "non_empty", "label": "non empty", "status": "passed", "detail": ""}],
        warnings=[],
    )


def failed_audit() -> DeterministicAudit:
    return DeterministicAudit(
        checks=[{"key": "valid_json", "label": "json", "status": "failed", "detail": "invalid"}],
        warnings=[],
    )


def unverified_audit() -> DeterministicAudit:
    return DeterministicAudit(
        checks=[{"key": "freshness_grounding", "label": "fresh", "status": "unverified", "detail": "missing"}],
        warnings=[],
    )


def test_simple_transform_stays_single_inference_in_auto():
    plan = plan_verification(
        verification="auto",
        user_text="Исправь орфографию в этом предложении",
        route_mode="fast",
        requirements=[],
        freshness_required=False,
        verified_source_count=0,
        answer="Исправленный текст.",
        deterministic=clean_audit(),
    )
    assert plan.run_critic is False
    assert plan.repair_deterministic is False
    assert plan.extra_inference_budget == 0


def test_scope_lock_reserves_one_possible_repair_without_forcing_critic():
    plan = plan_verification(
        verification="auto",
        user_text="Исправь только ошибки, ничего не добавляй:\n\nИсходный текст для правки.",
        route_mode="fast",
        requirements=[],
        freshness_required=False,
        verified_source_count=0,
    )
    assert "scope_lock" in plan.reasons
    assert plan.extra_inference_budget >= 1
    assert plan.run_critic is False


def test_explicit_requirement_reserves_repair_compute_before_generation():
    plan = plan_verification(
        verification="auto",
        user_text="Верни JSON",
        route_mode="fast",
        requirements=[AnswerRequirement(kind="valid_json")],
        freshness_required=False,
        verified_source_count=0,
    )
    assert plan.extra_inference_budget >= 1


def test_high_risk_audit_runs_conditional_critic():
    plan = plan_verification(
        verification="auto",
        user_text="Проведи аудит безопасности production архитектуры",
        route_mode="deep",
        requirements=[],
        freshness_required=False,
        verified_source_count=0,
        answer="Подробный результат аудита.",
        deterministic=clean_audit(),
    )
    assert plan.risk_score >= 3
    assert plan.run_critic is True
    assert plan.repair_critic is True


def test_current_fact_without_evidence_is_high_risk():
    plan = plan_verification(
        verification="auto",
        user_text="Какая сейчас ключевая ставка?",
        route_mode="work",
        requirements=[],
        freshness_required=True,
        verified_source_count=0,
        answer="Ответ без источника",
        deterministic=unverified_audit(),
    )
    assert plan.run_critic is True
    assert "fresh_evidence_missing" in plan.reasons


def test_deterministic_failure_repairs_without_paying_for_critic_first():
    plan = plan_verification(
        verification="auto",
        user_text="Верни только JSON",
        route_mode="work",
        requirements=[AnswerRequirement(kind="valid_json")],
        freshness_required=False,
        verified_source_count=0,
        answer="not json",
        deterministic=failed_audit(),
    )
    assert plan.repair_deterministic is True
    assert plan.run_critic is False


def test_strict_always_requests_critic_but_is_bounded():
    plan = plan_verification(
        verification="strict",
        user_text="Обычный вопрос",
        route_mode="fast",
        requirements=[],
        freshness_required=False,
        verified_source_count=0,
        answer="Ответ",
        deterministic=clean_audit(),
    )
    assert plan.run_critic is True
    assert plan.extra_inference_budget == 2


def test_only_major_or_critical_critic_findings_trigger_repair():
    minor = {"ok": True, "issues": [{"severity": "minor", "message": "style"}]}
    major = {"ok": True, "issues": [{"severity": "major", "message": "missed requirement"}]}
    assert critic_has_repairable_issue(minor) is False
    assert critic_has_repairable_issue(major) is True


def test_critic_findings_become_explicit_repair_targets():
    audit = audit_with_critic_issues(
        clean_audit(),
        {"ok": True, "issues": [{"severity": "critical", "message": "contradiction"}]},
    )
    assert audit.failed is True
    assert any(item["key"].startswith("critic_issue_") for item in audit.checks)


def test_chat_enforces_two_extra_inference_cap_and_recalculates_plan():
    source = Path("app/api/routes/chat.py").read_text(encoding="utf-8")
    assert "_MAX_VERIFICATION_EXTRA_INFERENCES = 2" in source
    assert "verification_extra_inferences < _MAX_VERIFICATION_EXTRA_INFERENCES" in source
    assert source.count("plan_verification(") >= 3
    assert "audit_with_critic_issues(deterministic, critic)" in source
    assert "critic_has_repairable_issue(critic)" in source


def test_verification_telemetry_is_public_and_audited():
    schema = Path("app/schemas/chat.py").read_text(encoding="utf-8")
    chat = Path("app/api/routes/chat.py").read_text(encoding="utf-8")
    for marker in (
        "verification_risk_score",
        "verification_extra_inferences",
        "critic_used",
        "repair_applied",
    ):
        assert marker in schema
        assert marker in chat
    assert '"conditional_verification"' in chat


def test_auto_does_not_use_old_strict_only_critic_gate():
    source = Path("app/api/routes/chat.py").read_text(encoding="utf-8")
    assert 'payload.verification == "strict" and deterministic' not in source
    assert "plan.run_critic" in source
