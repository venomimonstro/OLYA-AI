#!/usr/bin/env python3
from __future__ import annotations

import json

# Install the production bootstrap wrappers before importing routing functions.
import app.api.routes  # noqa: F401
from app.atomic_fact_latency_patch import is_stable_atomic_fact
from app.inference import router as inference_router
from app.inference.client import LlamaClient
from app.services.conditional_verification import plan_verification
from app.services.fast_web_grounding import should_auto_ground
from app.services.freshness import classify_freshness
from app.services.interactive_evidence import InteractiveEvidence, reset_interactive_evidence, set_interactive_evidence
from app.services.structured_facts import is_currency_rate_question
from app.utility_chat import utility_reply


def _route(text: str, mode: str):
    return inference_router.choose_route(text, mode, 4096, 4096)


def audit() -> dict:
    errors: list[str] = []
    matrix: list[dict] = []

    cases = [
        ("привет", "utility", False, False),
        ("кто написал мастер и маргарита?", "stable_fact", True, False),
        ("какая столица Франции?", "stable_fact", True, False),
        ("кто сейчас президент США?", "official_role", True, True),
        ("какой курс доллар рубль сейчас?", "market", True, True),
        ("какая сегодня погода в Москве?", "weather", True, True),
        ("какая сейчас ключевая ставка?", "market", True, True),
        ("какая последняя версия Python?", "software_version", True, True),
        ("что такое HTTP и как он работает?", "direct", False, False),
        ("переведи этот текст на английский", "direct", False, False),
        ("напиши поздравление с днём рождения", "direct", False, False),
    ]

    for question, expected_kind, expected_web, expected_fresh in cases:
        fresh = classify_freshness(question)
        web = should_auto_ground(question)
        utility = utility_reply(question) is not None
        structured = is_currency_rate_question(question)
        atomic = is_stable_atomic_fact(question)
        if expected_kind == "utility":
            actual_kind = "utility" if utility else "direct"
        elif fresh.required:
            actual_kind = fresh.category
        elif atomic:
            actual_kind = "stable_fact"
        else:
            actual_kind = "direct"
        if actual_kind != expected_kind:
            errors.append(f"classification:{question}:{actual_kind}!={expected_kind}")
        if web != expected_web:
            errors.append(f"web_policy:{question}:{web}!={expected_web}")
        if bool(fresh.required) != expected_fresh:
            errors.append(f"freshness:{question}:{fresh.required}!={expected_fresh}")
        if "курс доллар рубль" in question and not structured:
            errors.append("currency_structured_resolver_not_selected")
        matrix.append({
            "question": question,
            "kind": actual_kind,
            "web": web,
            "fresh": bool(fresh.required),
            "structured": structured,
            "stable_atomic": atomic,
        })

    simple = _route("объясни что такое HTTP простыми словами", "fast")
    medium = _route("проанализируй проблему, сравни варианты и разработай стратегию исправления", "work")
    high = _route("проведи архитектурный аудит системы, найди риски и разработай план миграции", "deep")
    if simple.mode != "fast" or simple.reasoning:
        errors.append("simple_profile_not_fast_non_reasoning")
    if medium.mode != "work" or not medium.reasoning:
        errors.append("medium_complex_profile_not_reasoning")
    if high.mode != "deep" or not high.reasoning:
        errors.append("high_profile_not_deep_reasoning")
    if not (simple.max_output_tokens < medium.max_output_tokens < high.max_output_tokens):
        errors.append("quality_output_budgets_not_monotonic")

    medium_thinking = LlamaClient._thinking_budget(medium.max_output_tokens) if medium.reasoning else 0
    high_thinking = LlamaClient._thinking_budget(high.max_output_tokens) if high.reasoning else 0
    if not (0 < medium_thinking < high_thinking <= 320):
        errors.append(f"private_reasoning_budget_invalid:{medium_thinking}:{high_thinking}")
    client = LlamaClient("http://127.0.0.1:9")
    try:
        high_payload = client._payload([], max_tokens=high.max_output_tokens, reasoning=True)
        simple_payload = client._payload([], max_tokens=simple.max_output_tokens, reasoning=False)
    finally:
        # No network request was made; close is async, so the audit avoids
        # instantiating a running event loop merely for payload inspection.
        try:
            client._client._transport = None  # type: ignore[attr-defined]
        except Exception:
            pass
    if int(high_payload.get("thinking_budget_tokens", -1)) != high_thinking:
        errors.append("high_payload_missing_reasoning_budget")
    if int(simple_payload.get("thinking_budget_tokens", -1)) != 0:
        errors.append("simple_payload_reasoning_not_disabled")

    for mode in ("fast", "work", "deep"):
        decision = _route("какой курс доллар рубль сейчас?", mode)
        if decision.mode != "fast" or decision.reasoning:
            errors.append(f"atomic_current_lookup_not_fast:{mode}")
        if decision.max_output_tokens > 220:
            errors.append(f"atomic_current_lookup_output_too_large:{mode}:{decision.max_output_tokens}")

    evidence = InteractiveEvidence(
        category="market",
        evidence_count=1,
        independent_hosts=1,
        urls=("https://www.cbr.ru/scripts/XML_daily.asp",),
        authoritative=True,
        resolved_answer="1 USD = test RUB",
        source_kind="structured_official",
    )
    token = set_interactive_evidence(evidence)
    try:
        plan = plan_verification(
            verification="auto",
            user_text="какой курс доллар рубль сейчас?",
            route_mode="fast",
            requirements=[],
            freshness_required=True,
            verified_source_count=1,
            answer="Тестовый подтверждённый ответ",
            deterministic=None,
        )
    finally:
        reset_interactive_evidence(token)
    if plan.run_critic or plan.repair_critic or plan.repair_deterministic:
        errors.append("trusted_atomic_fact_still_triggers_extra_inference")
    if plan.extra_inference_budget != 0:
        errors.append("trusted_atomic_fact_extra_inference_budget_nonzero")

    return {
        "format": "olya-answer-pipeline-audit-v2",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "matrix": matrix,
        "quality_profiles": {
            "simple": {
                "mode": simple.mode,
                "reasoning": simple.reasoning,
                "max_output_tokens": simple.max_output_tokens,
                "thinking_budget_tokens": 0,
            },
            "medium": {
                "mode": medium.mode,
                "reasoning": medium.reasoning,
                "max_output_tokens": medium.max_output_tokens,
                "thinking_budget_tokens": medium_thinking,
            },
            "high": {
                "mode": high.mode,
                "reasoning": high.reasoning,
                "max_output_tokens": high.max_output_tokens,
                "thinking_budget_tokens": high_thinking,
            },
        },
        "trusted_atomic_extra_inferences": plan.extra_inference_budget,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
