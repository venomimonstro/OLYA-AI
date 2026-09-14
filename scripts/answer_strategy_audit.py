#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import app.api.routes  # noqa: F401 - install runtime patches
from app import answer_strategy_patch
from app.inference.client import LlamaClient
from app.services import fast_web_grounding
from app.services.project_context import ProjectContextBuilder
from app.services.searxng_discovery import SearxngDiscovery, _payload_marks_engine_unresponsive


ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[str] = []
    cases = {
        "practical_chess": "что нужно знать чтобы часто побеждать в шахматах",
        "stable_fact": "кто написал мастер и маргарита",
        "explain": "что такое HTTP и как он работает",
        "compare": "что лучше PostgreSQL или MySQL для интернет-магазина",
        "writing": "напиши поздравление с днем рождения",
        "short": "кратко: столица Франции?",
    }
    shapes = {name: answer_strategy_patch._answer_shape(question) for name, question in cases.items()}

    if not answer_strategy_patch._knowledge_synthesis(cases["practical_chess"]): errors.append("chess_not_knowledge_synthesis")
    if not answer_strategy_patch._stable_atomic(cases["stable_fact"]): errors.append("stable_fact_not_fast_atomic")
    if not fast_web_grounding.should_auto_ground(cases["practical_chess"]): errors.append("chess_not_auto_grounded")
    if "6-9 actionable points" not in shapes["practical_chess"]: errors.append("practical_depth_missing")
    if "4-7 key points" not in shapes["explain"]: errors.append("explain_depth_missing")
    if "recommendation first" not in shapes["compare"]: errors.append("comparison_shape_missing")
    if "no research" not in shapes["writing"].casefold(): errors.append("writing_search_guard_missing")
    if "1-4 sentences" not in shapes["short"]: errors.append("explicit_short_not_respected")

    if tuple(SearxngDiscovery.primary_engines) != ("google", "yandex"): errors.append("primary_search_not_google_yandex")
    if tuple(SearxngDiscovery.fallback_engines) != ("bing", "startpage"): errors.append("search_fallback_order")
    if not _payload_marks_engine_unresponsive({"unresponsive_engines": [["google", "CAPTCHA"]]}, "google"):
        errors.append("google_captcha_not_detected")
    if not _payload_marks_engine_unresponsive({"unresponsive_engines": [{"engine": "yandex", "reason": "parse"}]}, "yandex"):
        errors.append("yandex_failure_not_detected")

    context = ProjectContextBuilder()
    if context.hot_history_messages > 6: errors.append("hot_history_too_large")
    if context.max_memories > 12: errors.append("project_memory_prompt_too_large")

    source_text = (ROOT / "app" / "answer_strategy_patch.py").read_text("utf-8")
    if "Dynamic answer-shape instructions MUST stay at the tail" not in source_text:
        errors.append("dynamic_prompt_tail_contract_missing")
    memory_text = (ROOT / "app" / "services" / "long_term_memory.py").read_text("utf-8")
    if "max_chars=1100" not in memory_text or "limit=4" not in memory_text:
        errors.append("cpu_memory_budget_missing")

    settings = (ROOT / "searxng" / "settings.yml").read_text("utf-8")
    for engine in ("google", "yandex", "bing", "startpage"):
        if f"- {engine}" not in settings: errors.append(f"searx_engine_missing:{engine}")
    if "keep_only:" not in settings: errors.append("searx_keep_only_missing")

    checks = {
        "chess_auto_web": fast_web_grounding.should_auto_ground(cases["practical_chess"]),
        "stable_fact_fast_path": answer_strategy_patch._stable_atomic(cases["stable_fact"]),
        "practical_shape": shapes["practical_chess"],
        "explain_shape": shapes["explain"],
        "compare_shape": shapes["compare"],
        "primary_engines": list(SearxngDiscovery.primary_engines),
        "fallback_engines": list(SearxngDiscovery.fallback_engines),
        "hot_history_messages": context.hot_history_messages,
        "max_project_memories": context.max_memories,
        "compact_synthesis_installed": bool(getattr(fast_web_grounding.execute_fast_web_grounding, "_olya_compact_synthesis", False)),
        "source_appendix_installed": bool(getattr(LlamaClient.generate, "_olya_source_appendix", False)),
        "google_captcha_detected": _payload_marks_engine_unresponsive({"unresponsive_engines": [["google", "CAPTCHA"]]}, "google"),
    }
    if not checks["compact_synthesis_installed"]: errors.append("compact_synthesis_not_installed")
    if not checks["source_appendix_installed"]: errors.append("source_appendix_not_installed")

    return {
        "format": "olya-answer-strategy-audit-v3",
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
