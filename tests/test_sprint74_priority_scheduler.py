from __future__ import annotations

import asyncio
from time import monotonic

import pytest

from app.services.resource_governor import (
    ResourceBusyError,
    ResourceGovernor,
    _Waiter,
    inference_scheduler_context,
    set_inference_scheduler_context,
)
from scripts.priority_scheduler_audit import audit


def test_sprint74_scheduler_context_is_request_local_contract() -> None:
    set_inference_scheduler_context(priority_class="api", plan="pro", principal="u-1", channel="api")
    assert inference_scheduler_context() == {
        "priority_class": "api",
        "plan": "pro",
        "principal": "u-1",
        "channel": "api",
    }


def test_sprint74_plan_boost_changes_order_without_capacity_change() -> None:
    governor = ResourceGovernor(max_concurrent=1, max_queue=4, wait_timeout_seconds=2)
    now = monotonic()
    free = _Waiter(1, now, "work", "free", "free", "chat")
    business = _Waiter(2, now, "work", "business", "business", "chat")
    governor._waiters = [free, business]
    assert governor._next_waiter() is business
    assert governor.max_concurrent == 1


def test_sprint74_aging_prevents_background_starvation() -> None:
    governor = ResourceGovernor(max_concurrent=1, max_queue=4, wait_timeout_seconds=120)
    now = monotonic()
    old_background = _Waiter(1, now - 100, "background", "free", "u-old", "background")
    new_fast = _Waiter(2, now, "fast", "business", "u-new", "chat")
    assert governor._score(old_background, now) < governor._score(new_fast, now)


def test_sprint74_zero_queue_still_fails_closed() -> None:
    async def scenario() -> None:
        governor = ResourceGovernor(max_concurrent=1, max_queue=0, wait_timeout_seconds=1)
        async with governor.slot(priority_class="fast"):
            with pytest.raises(ResourceBusyError):
                async with governor.slot(priority_class="fast"):
                    pass

    asyncio.run(scenario())


def test_sprint74_contract_audit_passes() -> None:
    result = audit()
    assert result["status"] == "passed", result["errors"]
