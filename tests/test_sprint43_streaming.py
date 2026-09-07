from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.api.routes.chat import _primary_generation
from app.inference.client import LlamaClient
from app.schemas.chat import ChatMessage, ChatUsage
from app.services.resource_governor import ResourceGovernor

ROOT = Path(__file__).resolve().parents[1]


class _FakeResponse:
    def __init__(self, lines):
        self.lines = list(lines)

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self.lines:
            await asyncio.sleep(0)
            yield line


class _StreamContext:
    def __init__(self, response):
        self.response = response
        self.closed = False

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False


class _FakeClient:
    def __init__(self, response):
        self.context = _StreamContext(response)
        self.payload = None

    def stream(self, method, url, **kwargs):
        assert method == "POST"
        assert url.endswith("/v1/chat/completions")
        self.payload = kwargs.get("json")
        return self.context


class _BlockingResponse:
    def __init__(self, entered: asyncio.Event):
        self.entered = entered

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"A"}}]}'
        self.entered.set()
        await asyncio.Event().wait()


def _client_with(fake) -> LlamaClient:
    client = object.__new__(LlamaClient)
    client.base_url = "http://llama:8080"
    client._client = fake
    return client


def test_llama_streams_real_chunks_and_reports_transport_telemetry():
    async def scenario():
        fake = _FakeClient(
            _FakeResponse(
                [
                    'data: {"choices":[{"delta":{"content":"Привет"}}]}',
                    'data: {"choices":[{"delta":{"content":" мир"}}],"usage":{"completion_tokens":2},"timings":{"predicted_per_second":12.5}}',
                    "data: [DONE]",
                ]
            )
        )
        client = _client_with(fake)
        chunks = []

        async def sink(text: str):
            chunks.append(text)

        result = await client.generate(
            [ChatMessage(role="user", content="Привет")],
            max_tokens=64,
            reasoning=False,
            on_token=sink,
        )
        assert chunks == ["Привет", " мир"]
        assert result.text == "Привет мир"
        assert result.output_tokens == 2
        assert result.tokens_per_second == 12.5
        assert result.ttft_ms >= 0
        assert fake.payload["stream"] is True
        assert fake.payload["chat_template_kwargs"]["enable_thinking"] is False
        assert fake.context.closed is True

    asyncio.run(scenario())


def test_cancel_closes_upstream_http_stream():
    async def scenario():
        entered = asyncio.Event()
        fake = _FakeClient(_BlockingResponse(entered))
        client = _client_with(fake)
        task = asyncio.create_task(
            client.generate(
                [ChatMessage(role="user", content="Продолжай")],
                max_tokens=128,
                reasoning=False,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("generation task must be cancelled")
        assert fake.context.closed is True

    asyncio.run(scenario())


def test_cancelled_generation_releases_governor_slot():
    async def scenario():
        governor = ResourceGovernor(max_concurrent=1, max_queue=1, wait_timeout_seconds=1)
        entered = asyncio.Event()
        blocker = asyncio.Event()

        async def worker():
            async with governor.slot():
                entered.set()
                await blocker.wait()

        task = asyncio.create_task(worker())
        await entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        async with governor.slot():
            assert governor.waiting == 0

    asyncio.run(scenario())


def test_legacy_chat_only_test_backend_remains_compatible():
    class ChatOnly:
        async def chat(self, messages, *, max_tokens, reasoning):
            assert max_tokens == 32
            assert reasoning is False
            return "legacy ok"

    async def scenario():
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(llama=ChatOnly()))
        )
        chunks = []

        async def sink(text: str):
            chunks.append(text)

        result = await _primary_generation(
            request,
            [ChatMessage(role="user", content="test")],
            max_tokens=32,
            reasoning=False,
            on_token=sink,
        )
        assert result.text == "legacy ok"
        assert chunks == ["legacy ok"]

    asyncio.run(scenario())


def test_stream_route_emits_tokens_result_and_cancels_worker():
    route = (ROOT / "app/api/routes/chat.py").read_text("utf-8")
    for marker in (
        'outbound.put(("token", {"text": text}))',
        'outbound.put(("replace", {"text": text}))',
        'yield _sse("result"',
        "task.cancel()",
        "on_token=emit_token",
        "on_replace=emit_replace",
        "callable(generate)",
    ):
        assert marker in route
    assert "await _primary_generation(" in route


def test_browser_consumes_sse_and_stop_aborts_fetch():
    ui = (ROOT / "app/user_ui.py").read_text("utf-8")
    for marker in (
        "streamChatEndpoint='/v1/chat/stream'",
        "new AbortController()",
        "item.event==='token'",
        "item.event==='replace'",
        "activeController.abort()",
        "TTFT:",
        "ток/с",
    ):
        assert marker in ui
    assert ".innerHTML" not in ui


def test_chat_usage_exposes_streaming_telemetry_without_breaking_old_clients():
    usage = ChatUsage(raw_message_chars=10, compiled_message_chars=10, mode="fast")
    assert usage.queue_ms == 0
    assert usage.ttft_ms is None
    assert usage.output_tokens == 0
    assert usage.tokens_per_second is None
