#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from time import perf_counter
from urllib.parse import urlsplit

import app.api.routes  # noqa: F401 - install runtime search policy
from app.services.searxng_discovery import SearxngDiscovery
from app.services import task_solver


CASES = (
    {
        "query": "current President of the United States site:whitehouse.gov",
        "language": "en",
        "required_host": "whitehouse.gov",
        "expected_terms": ("president", "administration", "white house"),
        "min_domains": 1,
    },
    {
        "query": "кто написал мастер и маргарита Булгаков",
        "language": "ru",
        "required_host": "",
        "expected_terms": ("булгаков", "мастер", "маргарита"),
        "min_domains": 2,
    },
    {
        "query": "latest Python version site:python.org",
        "language": "en",
        "required_host": "python.org",
        "expected_terms": ("python", "download", "release"),
        "min_domains": 1,
    },
)


def _host(url: str) -> str:
    return (urlsplit(str(url or "")).hostname or "").casefold().removeprefix("www.")


def _host_ok(host: str, required: str) -> bool:
    return not required or host == required or host.endswith("." + required)


def _text(row: dict) -> str:
    return " ".join((str(row.get("title") or ""), str(row.get("snippet") or ""), str(row.get("url") or ""))).casefold()


async def one(discovery: SearxngDiscovery, case: dict) -> dict:
    query = str(case["query"])
    started = perf_counter()
    try:
        hits = await discovery.search(query, count=8, country="RU", language=str(case["language"]))
    except Exception as exc:
        return {
            "query": query,
            "ok": False,
            "elapsed_ms": int((perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }

    ranked = task_solver.diversify_hits(hits, kind="web_research", limit=4)
    selected = []
    for row in ranked:
        host = _host(str(row.get("url") or ""))
        text = _text(row)
        term_hits = [term for term in case["expected_terms"] if str(term).casefold() in text]
        selected.append({
            "title": row.get("title"),
            "url": row.get("url"),
            "domain": host,
            "provider": row.get("provider"),
            "source_kind": row.get("source_kind"),
            "discovery_score": row.get("discovery_score"),
            "term_hits": term_hits,
        })

    required_host = str(case["required_host"])
    host_pass = any(_host_ok(str(row.get("domain") or ""), required_host) for row in selected) if required_host else True
    lexical_pass = any(row.get("term_hits") for row in selected)
    domains = {str(row.get("domain") or "") for row in selected if row.get("domain")}
    min_domains = max(1, int(case.get("min_domains") or 1))
    diversity_pass = len(domains) >= min_domains
    ok = bool(selected) and host_pass and lexical_pass and diversity_pass
    return {
        "query": query,
        "ok": ok,
        "elapsed_ms": int((perf_counter() - started) * 1000),
        "raw_results": len(hits),
        "selected": selected,
        "checks": {
            "required_host": required_host,
            "host_pass": host_pass,
            "lexical_pass": lexical_pass,
            "independent_domains": len(domains),
            "min_domains": min_domains,
            "diversity_pass": diversity_pass,
        },
    }


async def main_async() -> int:
    base = os.getenv("X1_SEARXNG_BASE_URL", "http://searxng:8080")
    discovery = SearxngDiscovery(base, timeout_seconds=3.0)
    rows: list[dict] = []
    # Run sequentially: the probe must measure production quality, not create a
    # synthetic burst that gets the server IP rate-limited by public engines.
    for case in CASES:
        rows.append(await one(discovery, case))
        await asyncio.sleep(0.15)
    errors = [row["query"] for row in rows if not row.get("ok")]
    result = {
        "format": "olya-search-quality-live-probe-v4",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "engine_pool": list(discovery.general_engines),
        "cases": rows,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
