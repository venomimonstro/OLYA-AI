#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from time import perf_counter

# Install the production patch order before importing the structured resolver.
import app.api.routes  # noqa: F401
from app.services import structured_facts


CASES = (
    ("fiat_fx", "какой курс доллар рубль сейчас?", 3500),
    ("key_rate", "какая сейчас ключевая ставка Банка России?", 3500),
    ("crypto", "сколько сейчас стоит биткоин?", 4000),
    ("weather", "какая погода сейчас в Москве?", 5000),
    ("local_time", "сколько времени сейчас в Токио?", 3500),
)


async def _case(kind: str, question: str, budget_ms: int) -> dict:
    started = perf_counter()
    result = None
    error = ""
    try:
        result = await structured_facts.resolve_structured_fact(question)
    except Exception as exc:  # probe must report rather than hide runtime failures
        error = f"{exc.__class__.__name__}: {exc}"
    elapsed = max(0, int((perf_counter() - started) * 1000))
    sources = list(getattr(result, "public_sources", []) or []) if result is not None else []
    domains = sorted({str(row.get("domain") or "") for row in sources if isinstance(row, dict) and row.get("domain")})
    answer = str(getattr(result, "resolved_answer", "") or "") if result is not None else ""
    authoritative = bool(getattr(result, "authoritative_evidence", False)) if result is not None else False
    ok = bool(result is not None and answer and sources and elapsed <= budget_ms)
    if kind == "crypto" and len(domains) < 2:
        ok = False
        if not error:
            error = "fewer_than_two_live_crypto_domains"
    return {
        "kind": kind,
        "question": question,
        "ok": ok,
        "elapsed_ms": elapsed,
        "budget_ms": budget_ms,
        "answer": answer[:600],
        "source_domains": domains,
        "source_count": len(sources),
        "authoritative": authoritative,
        "error": error,
    }


async def probe() -> dict:
    rows = await asyncio.gather(*(_case(*case) for case in CASES))
    failures = [row["kind"] for row in rows if not row["ok"]]
    # Structured providers are an optimization layer. A failed row means the
    # chat will use its generic multi-engine web fallback; report degraded rather
    # than implying the whole chat pipeline is unusable.
    return {
        "format": "olya-live-data-clusters-probe-v1",
        "status": "passed" if not failures else "degraded",
        "failed_structured_clusters": failures,
        "generic_web_fallback_on_failure": True,
        "cases": rows,
    }


def main() -> int:
    result = asyncio.run(probe())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
