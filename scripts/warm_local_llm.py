#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from time import perf_counter

import httpx

from app.services.project_context import _FAST_SYSTEM_PROMPT


async def main_async() -> int:
    base = str(os.getenv("X1_LLAMA_BASE_URL", "http://llama:8080") or "http://llama:8080").rstrip("/")
    model_name = str(os.getenv("X1_LLAMA_MODEL_NAME", "") or "")
    started = perf_counter()
    # Warm the exact prefix used by ordinary fast chat. llama.cpp prompt caching
    # can then reuse the system/Jinja prefix and evaluate mostly the new user
    # suffix instead of paying the cold prefix cost on the first real question.
    payload = {
        "model": "local",
        "messages": [
            {"role": "system", "content": _FAST_SYSTEM_PROMPT},
            {"role": "user", "content": "Ответь одним словом: готов."},
        ],
        "max_tokens": 4,
        "temperature": 0,
        "tool_choice": "none",
        "stream": False,
    }
    last_error = ""
    async with httpx.AsyncClient(trust_env=False) as client:
        for attempt in range(1, 4):
            try:
                health = await client.get(base + "/health", timeout=5.0)
                health.raise_for_status()
                response = await client.post(
                    base + "/v1/chat/completions",
                    json=payload,
                    timeout=httpx.Timeout(120.0, connect=5.0),
                )
                response.raise_for_status()
                data = response.json()
                text = str((((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
                if not text:
                    raise RuntimeError("warm-up returned empty content")
                elapsed_ms = int((perf_counter() - started) * 1000)
                timings = data.get("timings") or {}
                print(json.dumps({
                    "status": "ready",
                    "model": model_name,
                    "attempt": attempt,
                    "elapsed_ms": elapsed_ms,
                    "prompt_tokens_per_second": timings.get("prompt_per_second", 0),
                    "tokens_per_second": timings.get("predicted_per_second", 0),
                    "prefix": "olya_fast_chat_v1",
                }, ensure_ascii=False), flush=True)
                return 0
            except (httpx.HTTPError, ValueError, TypeError, RuntimeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < 3:
                    await asyncio.sleep(float(attempt))
    print(json.dumps({"status": "failed", "model": model_name, "error": last_error}, ensure_ascii=False), flush=True)
    return 2


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
