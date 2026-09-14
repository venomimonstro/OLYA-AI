#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from app.services.conditional_verification import plan_verification
from app.services.fast_web_grounding import should_auto_ground

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []

    factual_cases = (
        "Кто сейчас CEO OpenAI?",
        "Какая последняя версия Qwen?",
        "Сравни актуальные цены двух сервисов",
        "Правда ли сейчас действует этот тариф?",
    )
    for text in factual_cases:
        if not should_auto_ground(text):
            errors.append({"code": "auto_ground_missing", "case": text})

    local_cases = (
        "Перепиши это письмо дружелюбнее",
        "Переведи этот текст на английский",
        "Придумай название для проекта",
    )
    for text in local_cases:
        if should_auto_ground(text):
            errors.append({"code": "unnecessary_web_search", "case": text})

    ordinary_web = plan_verification(
        verification="auto",
        user_text="Какая сейчас цена подписки?",
        route_mode="work",
        requirements=(),
        freshness_required=True,
        verified_source_count=3,
    )
    if ordinary_web.run_critic:
        errors.append({"code": "ordinary_web_still_runs_expensive_critic", "score": ordinary_web.risk_score})

    smart = (ROOT / "app" / "api" / "routes" / "smart_chat.py").read_text("utf-8")
    fast = (ROOT / "app" / "services" / "fast_web_grounding.py").read_text("utf-8")
    ui = (ROOT / "app" / "workspace_client_v4.py").read_text("utf-8")
    composed = (ROOT / "app" / "task_solver_user_ui.py").read_text("utf-8")
    bootstrap = (ROOT / "app" / "api" / "__init__.py").read_text("utf-8")

    required = {
        "smart_fast_grounding": (smart, "execute_fast_web_grounding"),
        "smart_auto_fact_grounding": (smart, "should_auto_ground"),
        "search_status": (smart, "Ищу и сверяю источники"),
        "bounded_page_fetch": (fast, "asyncio.wait_for(fetcher.fetch(url), timeout=fetch_timeout)"),
        "three_page_cap": (fast, "fetch_rows = selected_rows[:3]"),
        "public_source_provider": (fast, '"provider": str(row.get("provider") or "search")'),
        "public_source_verified": (fast, '"verified": source is not None'),
        "light_chat_hover": (ui, "background:#e9e9ec!important"),
        "waiting_animation": (ui, "olya-waiting-mark"),
        "source_chips": (ui, "olya-source-chip"),
        "favicons": (ui, "u.origin+'/favicon.ico'"),
        "final_ui_composition": (composed, "enhance_workspace_v4(document)"),
        "forced_stream_scroll_removed": (composed, 'document.replace(forced_scroll, "renderMarkdown(live.bubble,raw)", 2)'),
        "project_access_guard": (bootstrap, "install_task_solver_access_patch()"),
    }
    for code, (source, token) in required.items():
        if token not in source:
            errors.append({"code": code, "missing": token})

    return {
        "format": "olya-chat-grounding-v4",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "factual_auto_ground_cases": len(factual_cases),
        "local_no_search_cases": len(local_cases),
        "ordinary_web_critic": ordinary_web.run_critic,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
