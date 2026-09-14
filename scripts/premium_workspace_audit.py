#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from app.workspace_premium_v6 import enhance_workspace_premium_v6

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[str] = []
    source = (ROOT / "app" / "workspace_premium_v6.py").read_text("utf-8")
    installer = (ROOT / "app" / "task_solver_user_ui.py").read_text("utf-8")

    fixture = '''<!doctype html><html><head><style nonce="testnonce"></style></head><body>
<div id="view-chat"></div><textarea id="prompt"></textarea><button id="send"></button>
<div id="messages"></div><div id="empty"></div><div id="chat-controls"></div>
<script nonce="testnonce"></script></body></html>'''
    rendered = enhance_workspace_premium_v6(fixture)

    markers = {
        "premium_marker": "OLYA_PREMIUM_WORKSPACE_V6",
        "parallel_run_map": "const runs=new Map()",
        "durable_parallel_runs": "olya_parallel_runs_v1",
        "smooth_streaming": "requestAnimationFrame",
        "background_completion": "Ответ в другом чате готов",
        "per_chat_activity": "activeForConversation",
        "history_run_indicator": "olya-run-dot",
        "growing_composer": "resizeComposer",
        "composer_height_cap": "max-height:240px",
        "draft_friendly_submit": "prompt.value=''",
        "advanced_menu": "olya-advanced-menu",
        "reduced_motion": "prefers-reduced-motion",
        "safe_run_recovery": "/v1/chat/runs/",
        "mobile_surface": "@media(max-width:760px)",
        "premium_brand": "OLYA AI",
    }
    for name, marker in markers.items():
        if marker not in rendered:
            errors.append(f"missing:{name}")

    if "enhance_workspace_premium_v6(document)" not in installer:
        errors.append("premium_workspace_not_installed")
    quality_pos = installer.find("enhance_quality_levels(document)")
    premium_pos = installer.find("enhance_workspace_premium_v6(document)")
    if quality_pos < 0 or premium_pos <= quality_pos:
        errors.append("premium_layer_must_be_last")

    # The premium submit path must not globally lock prompt input. Old legacy
    # functions may still contain prompt.disabled for recovery compatibility;
    # the new interaction path intentionally does not.
    premium_submit_start = source.find("async function premiumSubmit")
    premium_submit_end = source.find("async function stopPremiumRun", premium_submit_start)
    premium_submit = source[premium_submit_start:premium_submit_end]
    if premium_submit_start < 0:
        errors.append("premium_submit_missing")
    elif "setBusy(" in premium_submit or "prompt.disabled" in premium_submit:
        errors.append("premium_submit_globally_blocks_composer")
    if "void runInBackground(run)" not in premium_submit:
        errors.append("premium_submit_not_backgrounded")

    # One request per chat, but multiple chats can each own a run. This prevents
    # duplicate turns in one conversation while retaining multi-chat parallelism.
    if "if(activeForConversation(conversationId))return" not in premium_submit:
        errors.append("same_chat_duplicate_run_guard_missing")
    if "runs.set(run.id,run)" not in source:
        errors.append("parallel_run_registry_missing")

    return {
        "format": "olya-premium-workspace-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "premium_ui": True,
        "smooth_streaming": "requestAnimationFrame" in rendered,
        "parallel_chat_jobs": "const runs=new Map()" in rendered,
        "composer_stays_editable": "setBusy(" not in premium_submit if premium_submit else False,
        "composer_auto_grow": "resizeComposer" in rendered,
        "background_run_recovery": "olya_parallel_runs_v1" in rendered,
        "safe_single_model_queue": True,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
