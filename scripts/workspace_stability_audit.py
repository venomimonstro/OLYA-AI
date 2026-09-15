#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from app.db import SessionLocal
from app.task_solver_user_ui import workspace

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[str] = []
    recovery = (ROOT / "app" / "workspace_recovery_controls.py").read_text("utf-8")
    library = (ROOT / "app" / "workspace_chat_library_v1.py").read_text("utf-8")
    client = (ROOT / "app" / "workspace_client_v4.py").read_text("utf-8")
    route = (ROOT / "app" / "task_solver_user_ui.py").read_text("utf-8")

    try:
        with SessionLocal() as db:
            response = workspace(db)
        html = response.body.decode("utf-8")
        render_error = ""
    except Exception as exc:
        html = ""
        render_error = f"{exc.__class__.__name__}: {exc}"

    checks = {
        "workspace_renders": not render_error and bool(html),
        "recovery_v6": "OLYA_RECOVERY_CONTROLS_V6" in html,
        "thinking_outside_messages": "olya-thinking-host" in html and "composerWrap.parentNode.insertBefore(host,composerWrap)" in html,
        "single_waiting_owner": "olya-waiting-row" not in html and "olya-waiting-row" not in client,
        "message_observer_child_only": "observe(messages,{childList:true})" in html,
        "no_message_characterdata_loop": "observe(messages,{childList:true,subtree:true,characterData:true})" not in html,
        "no_client_characterdata_scan": "messagesObserver.observe(messages,{childList:true,subtree:true,characterData:true})" not in html,
        "source_observer_filtered": "nodeContainsSources" in html,
        "stream_markdown_throttled": "now-run.lastPaint>=90" in html and "now-run.lastPaint>=28" not in html,
        "finish_paint_bounded": "performance.now()-started<90" in html and "performance.now()-started<260" not in html,
        "metrika_message_observer_direct_only": "observe(x1messages,{childList:true,subtree:true})" not in html,
        "smooth_scroll_disabled": ".messages{scroll-behavior:auto!important}" in html,
        "thinking_timer_250ms": "},250)" in html,
        "chat_library_v3": "OLYA_CHAT_LIBRARY_V3" in html,
        "chat_refresh_coalesced": "refreshPromise" in html and "refreshQueued" in html,
        "projects_cached": "projectsCacheAt<30000" in html,
        "initial_history_30": "/messages?limit=30" in html and "/messages?limit=100" not in html,
        "legacy_chat_manager_not_imported": "chat_management_enhancer" not in route,
        "source_recovery_marker": "OLYA_RECOVERY_CONTROLS_V6" in recovery,
        "source_library_marker": "OLYA_CHAT_LIBRARY_V3" in library,
    }
    for key, ok in checks.items():
        if not ok:
            errors.append(key)
    if render_error:
        errors.append("workspace_render_error:" + render_error)
    return {
        "format": "olya-workspace-stability-audit-v4",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "checks": checks,
        "render_error": render_error,
        "rendered_chars": len(html),
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
