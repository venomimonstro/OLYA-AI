#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from time import perf_counter

import httpx

import app.api.routes  # noqa: F401 - installs authority-aware search patches
from app.services.discovery import SearchHit
from app.services import discovery as discovery_service
from app.services import task_solver


CASES = (
    ("current President of the United States site:whitehouse.gov", "en"),
    ("кто написал мастер и маргарита Булгаков", "ru"),
    ("latest Python version site:python.org", "en"),
)


async def one(client: httpx.AsyncClient, base: str, query: str, language: str) -> dict:
    started = perf_counter()
    try:
        response = await client.get(
            base.rstrip("/") + "/search",
            params={
                "q": query,
                "format": "json",
                "safesearch": 1,
                "pageno": 1,
                "categories": "general",
                "language": language,
            },
            timeout=httpx.Timeout(4.0, connect=1.5),
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "query": query,
            "ok": False,
            "elapsed_ms": int((perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }

    hits: list[SearchHit] = []
    for index, row in enumerate(payload.get("results") or [], start=1):
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        hits.append(SearchHit(
            query=query,
            title=str(row.get("title") or "")[:500],
            url=url,
            snippet=str(row.get("content") or row.get("snippet") or "")[:1200],
            rank=index,
            provider="searxng",
        ))
        if len(hits) >= 12:
            break

    ranked = task_solver.diversify_hits(hits, kind="web_research", limit=4)
    return {
        "query": query,
        "ok": bool(ranked),
        "elapsed_ms": int((perf_counter() - started) * 1000),
        "raw_results": len(hits),
        "selected": [
            {
                "title": row.get("title"),
                "url": row.get("url"),
                "source_kind": row.get("source_kind"),
                "discovery_score": row.get("discovery_score"),
            }
            for row in ranked
        ],
    }


async def main_async() -> int:
    base = os.getenv("X1_SEARXNG_BASE_URL", "http://searxng:8080")
    async with httpx.AsyncClient(trust_env=False) as client:
        rows = await asyncio.gather(*(one(client, base, query, lang) for query, lang in CASES))
    errors = [row["query"] for row in rows if not row.get("ok")]
    result = {
        "format": "olya-search-quality-live-probe-v1",
        "status": "passed" if not errors else "degraded",
        "errors": errors,
        "cases": rows,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
