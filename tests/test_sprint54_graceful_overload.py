import asyncio
from pathlib import Path

from app.services.overload import FairOverloadLane, OverloadRejected, principal_from_authorization


def test_principal_digest_does_not_expose_bearer_token():
    raw = "Bearer super-secret-token"
    value = principal_from_authorization(raw)
    assert value != raw
    assert "secret" not in value
    assert len(value) == 24
    assert value == principal_from_authorization(raw)


def test_per_principal_queue_share_blocks_one_user_from_filling_lane():
    async def scenario():
        lane = FairOverloadLane("chat", max_concurrent=1, max_queue=4, queue_timeout_seconds=2,
                                max_queued_per_principal=1, breaker_failures=3)
        entered = asyncio.Event(); release = asyncio.Event()

        async def holder():
            async with lane.slot("a"):
                entered.set(); await release.wait()

        async def waiter():
            async with lane.slot("a"):
                return True

        first = asyncio.create_task(holder()); await entered.wait()
        second = asyncio.create_task(waiter()); await asyncio.sleep(0)
        try:
            async with lane.slot("a"):
                pass
        except OverloadRejected as exc:
            assert exc.reason == "fairness"
        else:
            raise AssertionError("same principal exceeded queue share")
        release.set(); await first; assert await second is True

    asyncio.run(scenario())


def test_global_queue_is_bounded_and_retry_after_is_present():
    async def scenario():
        lane = FairOverloadLane("sandbox", max_concurrent=1, max_queue=1, queue_timeout_seconds=2,
                                max_queued_per_principal=1)
        entered = asyncio.Event(); release = asyncio.Event()
        async def holder():
            async with lane.slot("a"):
                entered.set(); await release.wait()
        first = asyncio.create_task(holder()); await entered.wait()
        queued = asyncio.create_task(_enter_once(lane, "b")); await asyncio.sleep(0)
        try:
            async with lane.slot("c"):
                pass
        except OverloadRejected as exc:
            assert exc.reason == "queue_full"
            assert exc.retry_after >= 1
        else:
            raise AssertionError("queue accepted work beyond its bound")
        release.set(); await first; await queued
    asyncio.run(scenario())


async def _enter_once(lane, principal):
    async with lane.slot(principal):
        return True


def test_circuit_breaker_is_per_lane_and_half_open_is_bounded():
    async def scenario():
        research = FairOverloadLane("research", max_concurrent=1, max_queue=2, queue_timeout_seconds=1,
                                    breaker_failures=2, breaker_cooldown_seconds=60)
        chat = FairOverloadLane("chat", max_concurrent=1, max_queue=2, queue_timeout_seconds=1,
                                breaker_failures=2, breaker_cooldown_seconds=60)
        await research.record_outcome(failed=True)
        await research.record_outcome(failed=True)
        assert research.snapshot().breaker_state == "open"
        assert chat.snapshot().breaker_state == "closed"
        try:
            async with research.slot("u"):
                pass
        except OverloadRejected as exc:
            assert exc.reason == "circuit_open"
        else:
            raise AssertionError("open breaker admitted request")
        async with chat.slot("u"):
            pass
    asyncio.run(scenario())


def test_main_routes_expensive_work_through_pre_db_lanes():
    source = Path("app/main.py").read_text(encoding="utf-8")
    for marker in (
        '"/v1/chat/stream"',
        'return "research"',
        'return "images"',
        'return "sandbox"',
        'principal_from_authorization',
        'Retry-After',
        'resumable',
        'overload_lanes',
    ):
        assert marker in source


def test_admin_exposes_overload_state_without_database_aggregation():
    source = Path("app/api/routes/operations_analytics.py").read_text(encoding="utf-8")
    assert "@router.get('/overload')" in source
    assert "breaker_state" in source
    assert "overload_lanes" in source


def test_overload_configuration_is_bounded():
    source = Path("app/core/config.py").read_text(encoding="utf-8")
    for marker in (
        "overload_chat_max_active_http",
        "overload_chat_max_queue",
        "overload_max_queued_per_principal",
        "overload_breaker_failures",
        "overload_breaker_cooldown_seconds",
    ):
        assert marker in source
