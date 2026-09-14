#!/usr/bin/env python3
from __future__ import annotations

import inspect
import json

import app.api.routes  # noqa: F401 - install the single GigaChat runtime profile
from app.api.routes import smart_chat
from app.gigachat31_runtime_patch import install_gigachat31_runtime_patch
from app.inference.client import LlamaClient
from app.schemas.chat import ChatMessage
from app.services.clean_web import should_use_web
from app.services.project_context import ProjectContextBuilder
from app.services.searxng_discovery import SearxngDiscovery


def audit() -> dict:
    errors: list[str] = []

    bootstrap = inspect.getsource(__import__("app.api.routes", fromlist=["dummy"]))
    forbidden_installers = (
        "install_answer_strategy_patch",
        "install_fresh_search_policy_patch",
        "install_ultrafast_fresh_web_patch",
        "install_quality_evidence_policy_patch",
        "install_high_risk_verification_patch",
        "install_qwen4b_runtime_patch",
        "install_reasoning_budget_patch",
    )
    for name in forbidden_installers:
        if name in bootstrap:
            errors.append(f"legacy_patch_still_bootstrapped:{name}")

    if "build_clean_web_context" not in inspect.getsource(smart_chat._smart_managed_runner):
        errors.append("clean_web_not_used_by_chat")
    if "verification\": \"off\"" not in inspect.getsource(smart_chat._smart_managed_runner):
        errors.append("interactive_chat_may_run_extra_llm_verification")

    if should_use_web("что такое HTTP?", "auto"):
        errors.append("ordinary_question_unnecessarily_searches")
    if should_use_web("как научиться лучше играть в шахматы?", "auto"):
        errors.append("evergreen_advice_unnecessarily_searches")
    if not should_use_web("какая погода в Москве сейчас?", "auto"):
        errors.append("current_question_not_searched")
    if not should_use_web("найди последние новости OpenAI", "auto"):
        errors.append("explicit_search_not_detected")
    if should_use_web("найди последние новости OpenAI", "off"):
        errors.append("web_off_not_respected")

    if tuple(SearxngDiscovery.primary_engines) != ("google", "yandex", "duckduckgo", "bing"):
        errors.append("unexpected_search_engines")
    if tuple(SearxngDiscovery.fallback_engines) != ("duckduckgo", "bing"):
        errors.append("unexpected_search_fallback")
    if SearxngDiscovery.max_results != 5:
        errors.append("search_not_top5")

    context = ProjectContextBuilder()
    if context.hot_history_messages > 6:
        errors.append("hot_history_too_large")
    if context.max_memories > 12:
        errors.append("project_memory_prompt_too_large")

    install_gigachat31_runtime_patch()
    fake = object.__new__(LlamaClient)
    payload = LlamaClient._payload(
        fake,
        [ChatMessage(role="system", content="system"), ChatMessage(role="user", content="Привет")],
        max_tokens=128,
        reasoning=False,
    )
    if payload.get("tool_choice") != "none":
        errors.append("gigachat_tool_choice_none_missing")
    if payload.get("temperature") != 0.0:
        errors.append("gigachat_temperature_not_zero")
    for forbidden in ("chat_template_kwargs", "reasoning_format", "thinking_budget_tokens", "reasoning_effort"):
        if forbidden in payload:
            errors.append(f"foreign_llm_field_present:{forbidden}")

    checks = {
        "ordinary_question_uses_web": should_use_web("что такое HTTP?", "auto"),
        "current_question_uses_web": should_use_web("какая погода в Москве сейчас?", "auto"),
        "primary_engines": list(SearxngDiscovery.primary_engines),
        "fallback_engines": list(SearxngDiscovery.fallback_engines),
        "max_search_results": SearxngDiscovery.max_results,
        "hot_history_messages": context.hot_history_messages,
        "max_project_memories": context.max_memories,
        "gigachat_tool_choice": payload.get("tool_choice"),
        "gigachat_temperature": payload.get("temperature"),
        "payload_fields": sorted(payload.keys()),
    }
    return {
        "format": "olya-clean-chat-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "checks": checks,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
