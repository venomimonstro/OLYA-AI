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
)
from app.utility_chat import utility_reply


def audit() -> dict:
    errors: list[str] = []
    checks: dict[str, object] = {}

    tokyo = utility_reply("Какое время в Токио сейчас?")
    checks["tokyo"] = None if tokyo is None else {"kind": tokyo.kind, "text": tokyo.text}
    if tokyo is None or tokyo.kind != "local_time" or not re.search(r"\b\d{2}:\d{2}\b", tokyo.text):
        errors.append("tokyo_not_instant_local_time")

    calc = utility_reply("Сколько будет 17 * 23? Ответь только числом.")
    checks["calculator"] = None if calc is None else {"kind": calc.kind, "text": calc.text}
    if calc is None or calc.kind != "calculator" or calc.text != "391":
        errors.append("calculator_fast_path_invalid")

    writer = "Кто написал роман Мастер и Маргарита?"
    writer_atomic = is_atomic_knowledge_question(writer)
    writer_cap = atomic_output_cap(writer)
    writer_route = choose_route(writer, "auto", 4096, 4096)
    checks["writer"] = {
        "atomic": writer_atomic,
        "web": should_use_web(writer, "auto"),
        "route_mode": writer_route.mode,
        "max_output_tokens": writer_route.max_output_tokens,
        "reason": writer_route.reason,
    }
    if not writer_atomic or should_use_web(writer, "auto") or writer_cap is None or writer_cap > 96:
        errors.append("writer_not_fast_atomic")
    if writer_route.mode != "fast" or writer_route.max_output_tokens > 96:
        errors.append("writer_route_not_latency_bounded")

    definition = "Что такое HTTP?"
    checks["definition"] = {
        "atomic": is_atomic_knowledge_question(definition),
        "independent_fast": is_independent_fast_question(definition),
        "cap": atomic_output_cap(definition),
    }
    if not is_atomic_knowledge_question(definition) or (atomic_output_cap(definition) or 999) > 180:
        errors.append("definition_not_compact_ai")

    current_role = "Кто сейчас президент США?"
    checks["current_role"] = {
        "fresh": requires_fresh_data(current_role),
        "atomic": is_atomic_knowledge_question(current_role),
        "web": should_use_web(current_role, "auto"),
    }
    if not requires_fresh_data(current_role) or is_atomic_knowledge_question(current_role) or not should_use_web(current_role, "auto"):
        errors.append("current_role_routing_invalid")

    chess = "Что нужно знать чтобы часто побеждать в шахматах?"
    checks["chess_advice"] = {"web": should_use_web(chess, "auto")}
    if not should_use_web(chess, "auto"):
        errors.append("advice_search_not_enabled")

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
    }
    if not all(checks["thinking_ui"].values()):
        errors.append("thinking_animation_invalid")

    return {
        "format": "olya-latency-path-audit-v1",
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
