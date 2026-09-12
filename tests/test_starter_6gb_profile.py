from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.inference.router import choose_route
from app.services.resource_governor import ResourceBusyError, ResourceGovernor, set_inference_scheduler_context
from scripts.starter_6gb_audit import audit

ROOT = Path(__file__).resolve().parents[1]


def test_starter_contract_audit_passes() -> None:
    result = audit()
    assert result["status"] == "passed", result["errors"]


def test_starter_router_bounds_output_and_context() -> None:
    fast = choose_route("Кратко объясни HTTP", "fast", 4096, 4096)
    work = choose_route("Проанализируй архитектуру API", "work", 4096, 4096)
    deep = choose_route("Проведи аудит безопасности", "deep", 4096, 4096)
    assert fast.max_context_tokens <= 4096 and fast.max_output_tokens <= 448
    assert work.max_context_tokens <= 4096 and work.max_output_tokens <= 768
    assert deep.max_context_tokens <= 4096 and deep.max_output_tokens <= 1024


@pytest.mark.asyncio
async def test_starter_queue_rejects_second_waiter_from_same_principal() -> None:
    governor = ResourceGovernor(max_concurrent=1, max_queue=16, wait_timeout_seconds=2, max_queued_per_principal=1)
    release = asyncio.Event()

    async def holder():
        set_inference_scheduler_context(priority_class="work", plan="free", principal="holder", channel="work")
        async with governor.slot():
            await release.wait()

    async def first_waiter():
        set_inference_scheduler_context(priority_class="work", plan="free", principal="same-user", channel="work")
        async with governor.slot():
            return True

    holder_task = asyncio.create_task(holder())
    await asyncio.sleep(0)
    waiter_task = asyncio.create_task(first_waiter())
    for _ in range(50):
        if governor.waiting == 1:
            break
        await asyncio.sleep(0.01)
    assert governor.waiting == 1

    set_inference_scheduler_context(priority_class="fast", plan="pro", principal="same-user", channel="fast")
    with pytest.raises(ResourceBusyError, match="already has a request waiting"):
        async with governor.slot():
            pass

    release.set()
    await holder_task
    assert await waiter_task is True


def test_starter_compose_has_no_required_mmproj() -> None:
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    assert "--mmproj" not in compose
    assert "q4_0" in compose
    assert "Qwen3-4B-Q4_K_M.gguf" in compose
