#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from app.current_fact_latency_patch import _is_concise_fresh_lookup

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []

    concise, cap = _is_concise_fresh_lookup("кто сейчас президент сша?", "auto")
    if not concise or cap > 160:
        errors.append({"code": "current_role_not_concise", "concise": concise, "cap": cap})
    detailed, _ = _is_concise_fresh_lookup("подробно проанализируй работу текущего президента сша", "auto")
    if detailed:
        errors.append({"code": "analysis_incorrectly_capped"})

    timeout_patch = (ROOT / "app" / "chat_reliability_patch.py").read_text("utf-8")
    ui_patch = (ROOT / "app" / "workspace_reliability_v5.py").read_text("utf-8")
    workspace = (ROOT / "app" / "task_solver_user_ui.py").read_text("utf-8")
    bootstrap = (ROOT / "app" / "api" / "routes" / "__init__.py").read_text("utf-8")

    required = {
        "server_wall_timeout": (timeout_patch, "_CHAT_RUN_WALL_TIMEOUT_SECONDS = 210.0"),
        "server_timeout_wait_for": (timeout_patch, "asyncio.wait_for"),
        "timeout_patch_installed": (bootstrap, "install_chat_reliability_patch()"),
        "latency_patch_installed": (bootstrap, "install_current_fact_latency_patch()"),
        "pending_local_storage": (ui_patch, "localStorage.setItem(pendingKey"),
        "pending_migration": (ui_patch, "sessionStorage.getItem(pendingKey)"),
        "draft_autosave": (ui_patch, "prompt.addEventListener('input',saveDraft)"),
        "pagehide_autosave": (ui_patch, "window.addEventListener('pagehide',saveDraft)"),
        "draft_restore": (ui_patch, "function restoreDraft()"),
        "active_conversation_restore": (ui_patch, "async function restoreActiveConversation()"),
        "active_conversation_remember": (ui_patch, "rememberActiveConversation()"),
        "logout_cleanup": (ui_patch, "clearLocalChatState()"),
        "workspace_reliability_installed": (workspace, "enhance_workspace_reliability_v5(document)"),
    }
    for code, (source, marker) in required.items():
        if marker not in source:
            errors.append({"code": code, "missing": marker})

    return {
        "format": "olya-chat-reliability-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "current_role_output_cap": cap,
        "chat_wall_timeout_seconds": 210,
        "draft_autosave": True,
        "conversation_restore": True,
        "pending_run_persistence": "localStorage",
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
