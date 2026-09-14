#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from pathlib import Path

# Production bootstrap installs the structured ResourceGovernor bypass.
import app.api.routes  # noqa: F401
from app.services.interactive_evidence import InteractiveEvidence, reset_interactive_evidence, set_interactive_evidence
from app.services.resource_governor import ResourceGovernor
from app.services.user_resource_governor import UserResourceGovernor
from app.workspace_premium_v6 import enhance_workspace_premium_v6

ROOT = Path(__file__).resolve().parents[1]


async def _concurrency_checks(errors: list[str]) -> dict:
    # A second normal model-backed chat should wait, not be rejected. The model
    # still remains single-concurrency on the starter node.
    user_governor = UserResourceGovernor(max_waiting_per_user=3)
    first_ready = asyncio.Event(); release_first = asyncio.Event(); second_ready = asyncio.Event()

    async def first_user_slot():
        async with user_governor.slot("audit-user", 1):
            first_ready.set(); await release_first.wait()

    async def second_user_slot():
        async with user_governor.slot("audit-user", 1):
            second_ready.set()

    first_task = asyncio.create_task(first_user_slot())
    await asyncio.wait_for(first_ready.wait(), timeout=1.0)
    second_task = asyncio.create_task(second_user_slot())
    await asyncio.sleep(0.04)
    queued_instead_of_rejected = not second_task.done() and not second_ready.is_set()
    if not queued_instead_of_rejected:
        errors.append("second_chat_not_safely_queued")
    release_first.set()
    await asyncio.wait_for(second_ready.wait(), timeout=1.0)
    await asyncio.gather(first_task, second_task)

    # A structured fact already resolved from external data never calls llama.cpp,
    # therefore it must bypass an occupied ResourceGovernor rather than waiting.
    global_governor = ResourceGovernor(max_concurrent=1, max_queue=0, wait_timeout_seconds=1.0)
    model_ready = asyncio.Event(); release_model = asyncio.Event()

    async def model_holder():
        async with global_governor.slot():
            model_ready.set(); await release_model.wait()

    holder_task = asyncio.create_task(model_holder())
    await asyncio.wait_for(model_ready.wait(), timeout=1.0)
    evidence = InteractiveEvidence(
        category="weather", evidence_count=1, independent_hosts=1,
        urls=("https://api.open-meteo.com/v1/forecast",), authoritative=False,
        resolved_answer="structured audit answer", source_kind="structured_live",
    )
    token = set_interactive_evidence(evidence)
    structured_bypass = False
    try:
        async with asyncio.timeout(0.25):
            async with global_governor.slot():
                structured_bypass = True
    except TimeoutError:
        errors.append("structured_fact_waited_for_model_queue")
    finally:
        reset_interactive_evidence(token)
        release_model.set()
        await holder_task

    return {
        "normal_second_chat_queued": queued_instead_of_rejected,
        "structured_fact_bypasses_model_queue": structured_bypass,
    }


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

    premium_submit_start = source.find("async function premiumSubmit")
    premium_submit_end = source.find("async function stopPremiumRun", premium_submit_start)
    premium_submit = source[premium_submit_start:premium_submit_end]
    if premium_submit_start < 0:
        errors.append("premium_submit_missing")
    elif "setBusy(" in premium_submit or "prompt.disabled" in premium_submit:
        errors.append("premium_submit_globally_blocks_composer")
    if "void runInBackground(run)" not in premium_submit:
        errors.append("premium_submit_not_backgrounded")
    if "if(activeForConversation(conversationId))return" not in premium_submit:
        errors.append("same_chat_duplicate_run_guard_missing")
    if "runs.set(run.id,run)" not in source:
        errors.append("parallel_run_registry_missing")

    concurrency = asyncio.run(_concurrency_checks(errors))
    return {
        "format": "olya-premium-workspace-audit-v2",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "premium_ui": True,
        "smooth_streaming": "requestAnimationFrame" in rendered,
        "parallel_chat_jobs": "const runs=new Map()" in rendered,
        "composer_stays_editable": "setBusy(" not in premium_submit if premium_submit else False,
        "composer_auto_grow": "resizeComposer" in rendered,
        "background_run_recovery": "olya_parallel_runs_v1" in rendered,
        "server_concurrency": concurrency,
        "model_decode_concurrency": "unchanged_safe_limit",
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
