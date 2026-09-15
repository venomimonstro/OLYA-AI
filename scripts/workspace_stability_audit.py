#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[str] = []
    recovery = (ROOT / "app" / "workspace_recovery_controls.py").read_text("utf-8")
    library = (ROOT / "app" / "workspace_chat_library_v1.py").read_text("utf-8")
    route = (ROOT / "app" / "task_solver_user_ui.py").read_text("utf-8")

    checks = {
        "recovery_v5": "OLYA_RECOVERY_CONTROLS_V5" in recovery,
        "thinking_outside_messages": "composerWrap.parentNode.insertBefore(host,composerWrap)" in recovery,
        "message_observer_child_only": "observe(messages,{childList:true})" in recovery,
        "no_message_characterdata_loop": "observe(messages,{childList:true,subtree:true,characterData:true})" not in recovery,
        "stream_markdown_throttled": 'replace("now-run.lastPaint>=28", "now-run.lastPaint>=90")' in recovery,
        "thinking_timer_250ms": "},250)" in recovery,
        "chat_library_v3": "OLYA_CHAT_LIBRARY_V3" in library,
        "chat_refresh_coalesced": "refreshPromise" in library and "refreshQueued" in library,
        "projects_cached": "projectsCacheAt<30000" in library,
        "legacy_chat_manager_not_imported": "chat_management_enhancer" not in route,
    }
    for key, ok in checks.items():
        if not ok:
            errors.append(key)
    return {
        "format": "olya-workspace-stability-audit-v1",
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
