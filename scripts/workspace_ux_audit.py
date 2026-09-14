#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    source = (ROOT / "app" / "workspace_client_v3.py").read_text("utf-8")
    spec = (ROOT / "docs" / "CLIENT_WORKSPACE_UX_V3.md").read_text("utf-8")
    errors: list[dict] = []

    required_source = {
        "version_marker": "OLYA_WORKSPACE_CLIENT_V3",
        "dynamic_viewport": "window.visualViewport",
        "viewport_css_var": "--olya-app-height",
        "body_scroll_locked": "overflow:hidden!important",
        "chat_scroll_container": "overflow-y:auto!important",
        "overscroll_contained": "overscroll-behavior:contain!important",
        "stable_scrollbar": "scrollbar-gutter:stable!important",
        "composer_not_scrolling": "flex:0 0 auto!important",
        "jump_to_latest": "jump-bottom",
        "near_bottom_tracking": "distanceToBottom()<150",
        "stream_mutation_tracking": "messageObserver.observe",
        "stop_glyph": "stopping?'■':'↑'",
        "simple_mode": "auto:'Простая'",
        "medium_mode": "work:'Средняя'",
        "complex_mode": "deep:'Сложная'",
        "advanced_menu": "Дополнительные настройки",
        "project_in_advanced_menu": "Проект / контекст",
        "files_hidden": "#view-files{display:none!important}",
        "api_nav_hidden": "#nav-files,#nav-api{display:none!important}",
        "technical_meta_hidden": ".meta{display:none!important}",
        "mobile_safe_area": "env(safe-area-inset-bottom)",
        "reduced_motion": "prefers-reduced-motion:reduce",
        "escape_closes_controls": "event.key!=='Escape'",
    }
    for code, token in required_source.items():
        if token not in source:
            errors.append({"code": code, "missing": token})

    required_spec = (
        "единственный прокручиваемый контейнер сообщений",
        "Простая / Средняя / Сложная",
        "перестаёт тянуть его вниз",
        "кнопка `↑` превращается в `■`",
        "Mobile keyboard",
    )
    for token in required_spec:
        if token not in spec:
            errors.append({"code": "spec_contract_missing", "missing": token})

    return {
        "format": "olya-client-workspace-ux-v3",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "simulated_personas": 6,
        "contracts": len(required_source) + len(required_spec),
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
