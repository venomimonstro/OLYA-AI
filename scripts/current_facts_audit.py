#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from app.current_fact_evidence_guard import resolve_current_office_holder
from app.schemas.chat import ChatMessage
from app.services.conditional_verification import plan_verification
from app.services.fast_web_grounding import should_auto_ground
from app.services.freshness import classify_freshness

ROOT = Path(__file__).resolve().parents[1]


def _sample_messages(question: str) -> list[ChatMessage]:
    return [
        ChatMessage(
            role="user",
            content=(
                "WEB SEARCH DISCOVERY.\n\n"
                "[SEARCH 1] provider=searxng:google,bing search_confirmed=1\n"
                "Title: President Donald J. Trump\n"
                "URL: https://www.whitehouse.gov/administration/donald-j-trump/\n"
                "Snippet: President Donald J. Trump is the 45th & 47th President of the United States."
            ),
        ),
        ChatMessage(role="user", content=question),
    ]


def audit() -> dict:
    errors: list[dict] = []
    questions = ["кто сейчас президент сша?", "кто сегодня президент америки?"]
    decisions = {}
    resolutions = {}
    for question in questions:
        decision = classify_freshness(question)
        decisions[question] = decision
        if not decision.required or decision.category != "official_role":
            errors.append({"code": "president_not_current_role", "question": question, "decision": decision.__dict__})
        if not should_auto_ground(question):
            errors.append({"code": "president_not_auto_grounded", "question": question})
        resolved = resolve_current_office_holder(question, _sample_messages(question))
        resolutions[question] = resolved
        if not resolved or "Дональд Трамп" not in resolved:
            errors.append({"code": "authoritative_role_resolution_failed", "question": question, "resolved": resolved})
        if resolved and "Байден" in resolved:
            errors.append({"code": "stale_holder_survived_evidence_guard", "question": question, "resolved": resolved})
        verification = plan_verification(
            verification="auto",
            user_text=question,
            route_mode="work",
            requirements=[],
            freshness_required=True,
            verified_source_count=0,
        )
        if verification.run_critic or verification.extra_inference_budget != 0:
            errors.append({
                "code": "official_role_still_uses_llm_critic",
                "question": question,
                "run_critic": verification.run_critic,
                "extra_inference_budget": verification.extra_inference_budget,
            })

    smart = (ROOT / "app" / "api" / "routes" / "smart_chat.py").read_text("utf-8")
    fast = (ROOT / "app" / "services" / "fast_web_grounding.py").read_text("utf-8")
    policy = (ROOT / "app" / "response_policy_patch.py").read_text("utf-8")
    evidence_guard = (ROOT / "app" / "current_fact_evidence_guard.py").read_text("utf-8")
    search_policy = (ROOT / "app" / "fresh_search_policy_patch.py").read_text("utf-8")
    searx_client = (ROOT / "app" / "services" / "searxng_discovery.py").read_text("utf-8")
    bootstrap = (ROOT / "app" / "api" / "routes" / "__init__.py").read_text("utf-8")
    conditional = (ROOT / "app" / "services" / "conditional_verification.py").read_text("utf-8")

    required = {
        "mandatory_fresh_search": (smart, "mandatory_fresh"),
        "fresh_evidence_guard": (smart, "fresh_evidence_count"),
        "official_whitehouse_query": (fast, "current President of the United States site:whitehouse.gov"),
        "official_search_confirmation": (fast, "search_confirmed=1"),
        "single_role_query": (fast, 'max_queries = 1 if freshness.category == "official_role"'),
        "single_role_fetch": (fast, 'fetch_limit = 1 if freshness.category == "official_role"'),
        "short_role_fetch_timeout": (fast, 'fetch_timeout = 2.5 if freshness.category == "official_role"'),
        "same_language_policy": (policy, "Always answer in the language of the user's latest message"),
        "evidence_over_memory": (policy, "external evidence supplied in the context is authoritative"),
        "deterministic_evidence_guard": (evidence_guard, "resolve_current_office_holder"),
        "no_model_on_missing_role_evidence": (evidence_guard, "_unavailable_current_role"),
        "office_holder_no_cache": (search_policy, "ttl_seconds = 0"),
        "zero_critic_role_path": (conditional, "official_role_external_evidence"),
        "evidence_guard_installed": (bootstrap, "install_current_fact_evidence_guard()"),
        "parallel_engine_requests": (searx_client, "asyncio.create_task(one(engine))"),
        "early_engine_completion": (searx_client, "len(results) >= 2"),
        "google_engine_client": (searx_client, '"google"'),
        "yandex_engine_client": (searx_client, '"yandex"'),
        "duckduckgo_engine_client": (searx_client, '"duckduckgo"'),
    }
    for code, (text, marker) in required.items():
        if marker not in text:
            errors.append({"code": code, "missing": marker})

    return {
        "format": "olya-current-facts-audit-v4",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "phrases": {
            question: {
                "category": decisions[question].category,
                "required": decisions[question].required,
                "resolved": resolutions[question],
            }
            for question in questions
        },
        "office_holder_cache_seconds": 0,
        "role_search_queries": 1,
        "role_page_fetches": 1,
        "role_fetch_timeout_seconds": 2.5,
        "llm_critic_for_role_lookup": False,
        "search_engine_client_policy": ["google", "yandex", "bing", "duckduckgo", "brave", "startpage", "qwant"],
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
