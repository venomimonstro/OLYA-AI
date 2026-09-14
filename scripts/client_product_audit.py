#!/usr/bin/env python3
from __future__ import annotations

import inspect
import json

import app.main
from app.inference.router import choose_route
from app.schemas.conversations import ConversationResponse, ConversationUpdate
from app.services.long_term_memory import extract_user_memories


def audit() -> dict:
    errors: list[str] = []
    paths = {str(getattr(route, "path", "")) for route in app.main.app.routes}
    required = {
        "/app", "/memory", "/admin", "/admin/users", "/admin/chats",
        "/v1/memory", "/v1/memory/{memory_id}",
        "/v1/conversations", "/v1/conversations/{conversation_id}",
        "/v1/admin/chat-observer/conversations",
        "/v1/admin/chat-observer/conversations/{conversation_id}/messages",
    }
    for path in sorted(required - paths):
        errors.append(f"missing_route:{path}")

    if "pinned" not in ConversationUpdate.model_fields or "pinned" not in ConversationResponse.model_fields:
        errors.append("pin_contract_missing")

    long_form = choose_route(
        "Напиши подробную статью не менее 10000 символов про SEO продвижение",
        "auto", 4096, 4096,
    )
    if long_form.max_output_tokens < 3000 or "explicit_long_form" not in long_form.reason:
        errors.append("long_form_route_invalid")
    ordinary = choose_route("Что такое HTTP?", "auto", 4096, 4096)
    if ordinary.max_output_tokens > 1200:
        errors.append("ordinary_route_too_large")

    cases = {
        "explicit": "Запомни, что я предпочитаю короткие ответы без воды.",
        "preference": "Мне нравится, когда ответ начинается с вывода.",
        "decision": "Решили использовать PostgreSQL для проекта.",
        "fact": "Мой проект работает на сервере с 12 ГБ RAM.",
    }
    memory = {}
    for expected, text in cases.items():
        kinds = [row[0] for row in extract_user_memories(text)]
        memory[expected] = kinds
        if expected not in kinds:
            errors.append(f"memory_not_detected:{expected}")

    import app.services.memory_write_through as memory_write
    import app.workspace_recovery_controls as recovery
    import app.workspace_chat_library_v1 as library
    recovery_source = inspect.getsource(recovery.enhance_recovery_controls)
    library_source = inspect.getsource(library.enhance_chat_library)
    listener_source = inspect.getsource(memory_write.persist_user_memory_after_message)
    if "Остановить ответ" not in recovery_source or "↻" not in recovery_source or "✎" not in recovery_source:
        errors.append("recovery_controls_missing")
    if "pinned" not in library_source or "Удалить" not in library_source or "Переименовать" not in library_source:
        errors.append("chat_library_controls_missing")
    if "/memory" not in library_source:
        errors.append("memory_navigation_missing")
    if "after_insert" not in inspect.getsource(memory_write) or "extract_user_memories" not in listener_source:
        errors.append("memory_write_through_missing")

    return {
        "format": "olya-client-product-audit-v2",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "checks": {
            "long_form": {
                "mode": long_form.mode,
                "max_output_tokens": long_form.max_output_tokens,
                "reason": long_form.reason,
            },
            "ordinary": {
                "mode": ordinary.mode,
                "max_output_tokens": ordinary.max_output_tokens,
            },
            "memory_detection": memory,
            "memory_write_through": "extract_user_memories" in listener_source,
            "required_routes": sorted(required),
        },
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
