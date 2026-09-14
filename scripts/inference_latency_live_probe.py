#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os

# Install the same runtime generation policy used by chat.
import app.api.routes  # noqa: F401
from app.inference.client import LlamaClient
from app.schemas.chat import ChatMessage


async def _one(client: LlamaClient, *, name: str, prompt: str, max_tokens: int, reasoning: bool, expected: str) -> dict:
    errors: list[str] = []
    try:
        result = await client.generate(
            [
                ChatMessage(role="system", content="Отвечай на русском точно и без лишних вступлений."),
                ChatMessage(role="user", content=prompt),
            ],
            max_tokens=max_tokens,
            reasoning=reasoning,
            on_token=None,
        )
    except Exception as exc:
        return {
            "name": name,
            "status": "failed",
            "errors": [f"generation_failed:{exc.__class__.__name__}:{str(exc)[:180]}"],
        }

    text = result.text.strip()
    if expected and expected not in text:
        errors.append(f"expected_value_missing:{expected}")
    # Product thresholds, not merely liveness thresholds. If the starter CPU
    # cannot meet them, the audit must say so instead of calling a minute-long
    # response healthy.
    ttft_limit = 8000 if not reasoning else 18000
    total_limit = 30000 if not reasoning else 50000
    if result.ttft_ms > ttft_limit:
        errors.append(f"ttft_too_slow:{result.ttft_ms}>{ttft_limit}")
    if result.generation_ms > total_limit:
        errors.append(f"generation_too_slow:{result.generation_ms}>{total_limit}")
    if result.tokens_per_second and result.tokens_per_second < 3.0:
        errors.append(f"tokens_per_second_critically_low:{result.tokens_per_second}")
    return {
        "name": name,
        "status": "passed" if not errors else "degraded",
        "errors": errors,
        "reasoning": reasoning,
        "max_tokens": max_tokens,
        "thinking_budget_tokens": client._thinking_budget(max_tokens) if reasoning else 0,
        "ttft_ms": result.ttft_ms,
        "generation_ms": result.generation_ms,
        "output_tokens": result.output_tokens,
        "tokens_per_second": result.tokens_per_second,
        "answer": text[:500],
    }


async def probe() -> dict:
    base = os.getenv("X1_LLAMA_BASE_URL", "http://llama:8080").rstrip("/")
    client = LlamaClient(base, timeout_seconds=70)
    try:
        health = await client.health()
        if not health:
            return {
                "format": "olya-inference-latency-live-v1",
                "status": "failed",
                "errors": ["local_inference_health_failed"],
                "cases": [],
            }
        simple = await _one(
            client,
            name="simple",
            prompt="Сколько будет 17 умножить на 6? Ответь одним предложением.",
            max_tokens=96,
            reasoning=False,
            expected="102",
        )
        reasoning = await _one(
            client,
            name="bounded_reasoning",
            prompt="У Маши было 3 коробки по 4 яблока. Она отдала 5 яблок. Сколько яблок осталось? Ответь кратко.",
            max_tokens=260,
            reasoning=True,
            expected="7",
        )
    finally:
        await client.close()

    cases = [simple, reasoning]
    errors = [f"{case['name']}:{item}" for case in cases for item in case.get("errors", [])]
    return {
        "format": "olya-inference-latency-live-v1",
        "status": "passed" if not errors else "degraded",
        "errors": errors,
        "product_thresholds_ms": {
            "simple_ttft": 8000,
            "reasoning_ttft": 18000,
            "simple_total": 30000,
            "reasoning_total": 50000,
        },
        "cases": cases,
    }


def main() -> int:
    result = asyncio.run(probe())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
