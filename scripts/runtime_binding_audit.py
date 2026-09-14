#!/usr/bin/env python3
from __future__ import annotations

import inspect
import json

import app.main  # noqa: F401
from app.api.routes import smart_chat
from app.gigachat31_runtime_patch import _is_gigachat31
from app.services.clean_web import build_clean_web_context, should_use_web
from app.services.context import ContextCompiler
from app.services.project_context import ProjectContextBuilder
from app.services.searxng_discovery import SearxngDiscovery


def audit() -> dict:
    checks = {
        "gigachat31_runtime_selected": _is_gigachat31(),
        "smart_chat_uses_clean_web": "build_clean_web_context" in inspect.getsource(smart_chat._smart_managed_runner),
        "smart_chat_single_pass": "legacy_chat._chat_impl" in inspect.getsource(smart_chat._smart_managed_runner),
        "ordinary_question_skips_web": not should_use_web("что такое HTTP?", "auto"),
        "current_question_uses_web": should_use_web("погода в Москве сейчас", "auto"),
        "search_top5": SearxngDiscovery.max_results == 5,
        "context_compiler_clean": "answer_contract" not in inspect.getsource(ContextCompiler.compile),
        "project_context_has_system_prompt": "_SYSTEM_PROMPT" in inspect.getsource(ProjectContextBuilder.build),
        "clean_web_function_bound": callable(build_clean_web_context),
    }
    errors = [key for key, value in checks.items() if not value]
    result = {
        "format": "olya-runtime-binding-audit-v3",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "checks": checks,
    }
    return result


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
