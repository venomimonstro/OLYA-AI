from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Awaitable, Callable

import httpx

from app.schemas.chat import ChatMessage


class LlamaUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class LlamaGeneration:
    text: str
    ttft_ms: int
    output_tokens: int
    tokens_per_second: float
    generation_ms: int


TokenSink = Callable[[str], Awaitable[None]]


class LlamaClient:
    def __init__(self, base_url: str, timeout_seconds: int = 180) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(10.0, float(timeout_seconds)))
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            trust_env=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=30.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            response = await self._client.get(f"{self.base_url}/health", timeout=5.0)
            return response.is_success
        except httpx.HTTPError:
            return False

    @staticmethod
    def _sampling(reasoning: bool) -> dict:
        # Qwen3.6 is sensitive to over-constrained sampling. These are kept
        # explicit so a future model migration cannot silently inherit an
        # unsuitable generation profile.
        if reasoning:
            return {
                "temperature": 1.0,
                "top_p": 0.95,
                "top_k": 20,
                "min_p": 0.0,
                "presence_penalty": 1.5,
                "repeat_penalty": 1.0,
            }
        return {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "repeat_penalty": 1.0,
        }

    @staticmethod
    def _content_text(value) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts)
        return ""

    @classmethod
    def _chunk_text(cls, data: dict) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return ""
        choice = choices[0]
        delta = choice.get("delta")
        if isinstance(delta, dict):
            text = cls._content_text(delta.get("content"))
            if text:
                return text
        # Defensive fallback for servers that ignore stream=true but still send
        # an OpenAI-compatible message object on the same connection.
        message = choice.get("message")
        if isinstance(message, dict):
            return cls._content_text(message.get("content"))
        return ""

    @staticmethod
    def _telemetry(data: dict) -> tuple[int, float]:
        output_tokens = 0
        tokens_per_second = 0.0
        usage = data.get("usage")
        if isinstance(usage, dict):
            value = usage.get("completion_tokens")
            if isinstance(value, int) and value >= 0:
                output_tokens = value
        timings = data.get("timings")
        if isinstance(timings, dict):
            if not output_tokens:
                value = timings.get("predicted_n")
                if isinstance(value, int) and value >= 0:
                    output_tokens = value
            value = timings.get("predicted_per_second")
            if isinstance(value, (int, float)) and value >= 0:
                tokens_per_second = float(value)
        return output_tokens, tokens_per_second

    def _payload(self, messages: list[ChatMessage], *, max_tokens: int, reasoning: bool) -> dict:
        payload = {
            "model": "local",
            "messages": [message.model_dump() for message in messages],
            "max_tokens": max_tokens,
            "stream": True,
            **self._sampling(reasoning),
            # Qwen3.6 thinks by default. X1 explicitly owns this switch so Fast
            # and simple Work requests do not burn CPU/output budget on hidden
            # reasoning, while analytical Work/Deep can opt in.
            "chat_template_kwargs": {"enable_thinking": bool(reasoning)},
            "reasoning_format": "deepseek" if reasoning else "none",
        }
        if not reasoning:
            payload["reasoning_effort"] = "none"
        return payload

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int,
        reasoning: bool,
        on_token: TokenSink | None = None,
    ) -> LlamaGeneration:
        payload = self._payload(messages, max_tokens=max_tokens, reasoning=reasoning)
        started = perf_counter()
        first_content_at: float | None = None
        pieces: list[str] = []
        observed_chunks = 0
        completion_tokens = 0
        reported_tps = 0.0

        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        body = line[5:].strip()
                    elif line.startswith("{"):
                        body = line
                    else:
                        continue
                    if body == "[DONE]":
                        break
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue

                    chunk_tokens, chunk_tps = self._telemetry(data)
                    if chunk_tokens:
                        completion_tokens = chunk_tokens
                    if chunk_tps:
                        reported_tps = chunk_tps

                    text = self._chunk_text(data)
                    if not text:
                        continue
                    if first_content_at is None:
                        first_content_at = perf_counter()
                    pieces.append(text)
                    observed_chunks += 1
                    if on_token is not None:
                        # Awaiting the sink provides bounded backpressure. If the
                        # downstream request is cancelled, CancelledError escapes
                        # this block and the httpx stream context closes the
                        # upstream llama.cpp connection immediately.
                        await on_token(text)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlamaUnavailable("local llama.cpp inference is unavailable or returned invalid data") from exc

        finished = perf_counter()
        text = "".join(pieces).strip()
        if not text or first_content_at is None:
            raise LlamaUnavailable("local llama.cpp inference returned no final content")

        output_tokens = completion_tokens or observed_chunks
        if reported_tps > 0:
            tokens_per_second = reported_tps
        else:
            active_seconds = max(0.001, finished - first_content_at)
            tokens_per_second = float(output_tokens) / active_seconds
        return LlamaGeneration(
            text=text,
            ttft_ms=max(0, int((first_content_at - started) * 1000)),
            output_tokens=max(0, int(output_tokens)),
            tokens_per_second=max(0.0, round(tokens_per_second, 3)),
            generation_ms=max(0, int((finished - started) * 1000)),
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int,
        reasoning: bool,
    ) -> str:
        result = await self.generate(messages, max_tokens=max_tokens, reasoning=reasoning)
        return result.text
