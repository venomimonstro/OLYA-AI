#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import time

import httpx

from app.schemas.chat import ChatMessage
from app.services.context import ContextCompiler


def offline_probe(context_tokens: int, output_tokens: int) -> dict:
    compiler = ContextCompiler(max_chars=context_tokens * 4)
    # Russian-heavy synthetic context exercises the conservative chars/token
    # budget used by the chat route. Distinct markers at both ends prove that
    # compaction keeps the request intent rather than only its tail.
    payload = "НАЧАЛО_ЗАПРОСА\n" + ("Проверяем длинный русский контекст и сохранение смысла. " * 1800) + "\nКОНЕЦ_ЗАПРОСА"
    messages = [
        ChatMessage(role="system", content="X1 release-gate long-context probe."),
        ChatMessage(role="user", content=payload),
    ]
    prompt_budget_chars = max(1024, (context_tokens - output_tokens - 512) * 4)
    compiled = compiler.compile(messages, max_chars=prompt_budget_chars)
    text = "\n".join(item.content for item in compiled)
    total_chars = sum(len(item.content) for item in compiled)
    return {
        "status": "passed" if total_chars <= prompt_budget_chars and "НАЧАЛО_ЗАПРОСА" in text and "КОНЕЦ_ЗАПРОСА" in text else "failed",
        "context_tokens": context_tokens,
        "output_tokens": output_tokens,
        "prompt_budget_chars": prompt_budget_chars,
        "compiled_chars": total_chars,
        "kept_head_marker": "НАЧАЛО_ЗАПРОСА" in text,
        "kept_tail_marker": "КОНЕЦ_ЗАПРОСА" in text,
    }


async def live_probe(base_url: str, context_tokens: int, timeout: float) -> dict:
    # Keep enough prompt pressure to cross the old 8192-token configuration but
    # avoid consuming the entire model window. The probe asks for only 16 output
    # tokens so it is bounded even on a CPU-only node.
    target_chars = min(max(36_000, (context_tokens - 2048) * 3), 52_000)
    unit = "Контекст для проверки длинного окна модели. "
    body = (unit * (target_chars // len(unit) + 1))[:target_chars]
    prompt = f"НАЧАЛО\n{body}\nКОНЕЦ\nОтветь только словом OK."
    request = {
        "model": "local",
        "messages": [
            {"role": "system", "content": "Это техническая проверка окна контекста X1."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 16,
        "stream": False,
        "temperature": 0,
    }
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(base_url.rstrip("/") + "/v1/chat/completions", json=request)
        latency_ms = int((time.perf_counter() - started) * 1000)
        data = response.json() if response.content else {}
        content = str((((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
        return {
            "status": "passed" if response.is_success and bool(content) else "failed",
            "http_status": response.status_code,
            "latency_ms": latency_ms,
            "prompt_chars": len(prompt),
            "response_preview": content[:120],
        }
    except Exception as exc:
        return {"status": "failed", "error": type(exc).__name__, "detail": str(exc)[:500]}


def main() -> int:
    parser = argparse.ArgumentParser(description="X1 long-context release probe")
    parser.add_argument("--context-tokens", type=int, default=16384)
    parser.add_argument("--output-tokens", type=int, default=2200)
    parser.add_argument("--live-url", default="")
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()

    offline = offline_probe(args.context_tokens, args.output_tokens)
    live = asyncio.run(live_probe(args.live_url, args.context_tokens, args.timeout)) if args.live_url else {"status": "not_run"}
    overall = "failed" if offline["status"] != "passed" or live["status"] == "failed" else "passed"
    result = {"status": overall, "offline": offline, "live": live}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if overall == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
