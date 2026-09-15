#!/usr/bin/env python3
from __future__ import annotations

import inspect
import json
import re

from app.inference.router import choose_route
from app.services.clean_web import should_use_web
from app.services.response_strategy import (
    atomic_output_cap,
    is_atomic_knowledge_question,
    is_independent_fast_question,
    requires_fresh_data,
    requires_memory_context,
)
from app.utility_chat import utility_reply


def audit() -> dict:
    errors: list[str] = []
    checks: dict[str, object] = {}

    tokyo = utility_reply("Какое время в Токио сейчас?")
    tokyo_natural = utility_reply("Подскажите, пожалуйста, сколько в Токио времени? 🙏")
    checks["tokyo"] = {
        "canonical": None if tokyo is None else {"kind": tokyo.kind, "text": tokyo.text},
        "natural": None if tokyo_natural is None else {"kind": tokyo_natural.kind, "text": tokyo_natural.text},
    }
    for value in (tokyo, tokyo_natural):
        if value is None or value.kind != "local_time" or not re.search(r"\b\d{2}:\d{2}\b", value.text):
            errors.append("tokyo_not_instant_local_time")
            break

    calc = utility_reply("Сколько будет 17 * 23? Ответь только числом.")
    polite_calc = utility_reply("Подскажи, пожалуйста, сколько будет 17 * 23? Ответь только числом. 🙏")
    checks["calculator"] = {
        "canonical": None if calc is None else {"kind": calc.kind, "text": calc.text},
        "polite": None if polite_calc is None else {"kind": polite_calc.kind, "text": polite_calc.text},
    }
    if any(value is None or value.kind != "calculator" or value.text != "391" for value in (calc, polite_calc)):
        errors.append("calculator_fast_path_invalid")

    conversion = utility_reply("5 км в м")
    checks["conversion"] = None if conversion is None else {"kind": conversion.kind, "text": conversion.text}
    if conversion is None or conversion.kind != "unit_conversion" or "5000" not in conversion.text:
        errors.append("unit_conversion_fast_path_invalid")

    writer = "Кто написал роман Мастер и Маргарита?"
    polite_writer = "Подскажи, пожалуйста, кто написал роман Мастер и Маргарита?"
    writer_atomic = is_atomic_knowledge_question(writer)
    writer_polite_atomic = is_atomic_knowledge_question(polite_writer)
    writer_cap = atomic_output_cap(writer)
    writer_route = choose_route(writer, "auto", 4096, 4096)
    checks["writer"] = {
        "atomic": writer_atomic,
        "polite_atomic": writer_polite_atomic,
        "web": should_use_web(writer, "auto"),
        "route_mode": writer_route.mode,
        "max_output_tokens": writer_route.max_output_tokens,
        "reason": writer_route.reason,
    }
    if not writer_atomic or not writer_polite_atomic or should_use_web(writer, "auto") or writer_cap is None or writer_cap > 96:
        errors.append("writer_not_fast_atomic")
    if writer_route.mode != "fast" or writer_route.max_output_tokens > 96:
        errors.append("writer_route_not_latency_bounded")

    definition = "Что такое HTTP?"
    stable_rate_definition = "Что такое курс валют?"
    checks["definitions"] = {
        "http_atomic": is_atomic_knowledge_question(definition),
        "http_cap": atomic_output_cap(definition),
        "rate_atomic": is_atomic_knowledge_question(stable_rate_definition),
        "rate_web": should_use_web(stable_rate_definition, "auto"),
    }
    if not is_atomic_knowledge_question(definition) or (atomic_output_cap(definition) or 999) > 180:
        errors.append("definition_not_compact_ai")
    if not is_atomic_knowledge_question(stable_rate_definition) or should_use_web(stable_rate_definition, "auto"):
        errors.append("stable_definition_unnecessarily_uses_web")

    current_role = "Кто президент США?"
    historical_role = "Кто был первым президентом США?"
    checks["roles"] = {
        "current_fresh": requires_fresh_data(current_role),
        "current_web": should_use_web(current_role, "auto"),
        "historical_fresh": requires_fresh_data(historical_role),
        "historical_web": should_use_web(historical_role, "auto"),
    }
    if not requires_fresh_data(current_role) or not should_use_web(current_role, "auto"):
        errors.append("current_role_routing_invalid")
    if requires_fresh_data(historical_role) or should_use_web(historical_role, "auto"):
        errors.append("historical_role_unnecessarily_fresh")

    chess = "Что нужно знать, чтобы часто побеждать в шахматах?"
    import app.services.clean_web as clean_web
    advice_reads_pages = bool(clean_web._ADVICE_WEB_RE.search(chess))
    checks["chess_advice"] = {
        "web": should_use_web(chess, "auto"),
        "reads_pages": advice_reads_pages,
    }
    if not should_use_web(chess, "auto"):
        errors.append("advice_search_not_enabled")
    if not advice_reads_pages:
        errors.append("advice_page_reading_not_enabled")

    memory_question = "Что ты помнишь обо мне?"
    checks["memory"] = {"requires_memory": requires_memory_context(memory_question)}
    if not requires_memory_context(memory_question):
        errors.append("memory_query_not_detected")

    long_form = "Напиши статью не менее 10000 символов про SEO-продвижение интернет-магазина."
    long_route = choose_route(long_form, "auto", 4096, 4096)
    checks["long_form"] = {
        "mode": long_route.mode,
        "max_output_tokens": long_route.max_output_tokens,
        "reason": long_route.reason,
    }
    if long_route.max_output_tokens < 3000 or "explicit_long_form" not in long_route.reason:
        errors.append("natural_long_form_not_detected")

    import app.api.routes.smart_chat as smart_chat
    smart_source = inspect.getsource(smart_chat._smart_managed_runner)
    order = {
        "utility": smart_source.find("utility_reply(question)"),
        "structured": smart_source.find("is_live_structured_question(question)"),
        "web": smart_source.find("build_clean_web_context("),
    }
    checks["runner_order"] = order
    if min(order.values()) < 0 or not (order["utility"] < order["structured"] < order["web"]):
        errors.append("fast_path_order_invalid")
    if "retrieve_memories(" not in smart_source or "requires_memory_context(question)" not in smart_source:
        errors.append("new_chat_memory_injection_missing")

    import app.services.project_context as project_context
    checks["prompt_chars"] = {
        "fast": len(project_context._FAST_SYSTEM_PROMPT),
        "standard": len(project_context._SYSTEM_PROMPT),
    }
    if len(project_context._FAST_SYSTEM_PROMPT) > 420:
        errors.append("fast_system_prompt_too_large")
    builder_source = inspect.getsource(project_context.ProjectContextBuilder.build)
    if "is_independent_fast_question" not in builder_source or "if not context_needed" not in builder_source:
        errors.append("context_fast_skip_missing")

    import app.workspace_recovery_controls as recovery
    recovery_source = inspect.getsource(recovery.enhance_recovery_controls)
    checks["thinking_ui"] = {
        "three_dots": "olya-thinking-dots" in recovery_source,
        "state_labels": all(value in recovery_source for value in ("Проверяю факт", "Ищу и проверяю источники", "Формирую ответ")),
        "old_square_disabled": "content:none!important" in recovery_source,
        "stable_dom_move": "messages.lastElementChild!==thinking" in recovery_source,
    }
    if not all(checks["thinking_ui"].values()):
        errors.append("thinking_animation_invalid")

    import scripts.warm_local_llm as warm
    warm_source = inspect.getsource(warm.main_async)
    checks["warm_prefix"] = {
        "uses_fast_system_prompt": "_FAST_SYSTEM_PROMPT" in warm_source,
        "tool_choice_none": '"tool_choice": "none"' in warm_source,
    }
    if not all(checks["warm_prefix"].values()):
        errors.append("fast_prompt_warmup_missing")

    return {
        "format": "olya-latency-path-audit-v4",
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
