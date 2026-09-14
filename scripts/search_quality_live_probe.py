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
    },
    {
        "query": "кто написал мастер и маргарита Булгаков",
        "language": "ru",
        "required_host": "",
        "expected_terms": ("булгаков", "мастер", "маргарита"),
    },
    {
        "query": "latest Python version site:python.org",
        "language": "en",
        "required_host": "python.org",
        "expected_terms": ("python", "download", "release"),
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
        hits = await discovery.search(query, count=10, country="RU", language=str(case["language"]))
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
    # site: queries must actually return that site. General entity queries need
    # lexical evidence rather than merely any HTTP result.
    ok = bool(selected) and host_pass and lexical_pass
    return {
        "query": query,
        "ok": ok,
        "elapsed_ms": int((perf_counter() - started) * 1000),
        "raw_results": len(hits),
        "selected": selected,
        "checks": {"required_host": required_host, "host_pass": host_pass, "lexical_pass": lexical_pass},
    }


async def main_async() -> int:
    base = os.getenv("X1_SEARXNG_BASE_URL", "http://searxng:8080")
    discovery = SearxngDiscovery(base, timeout_seconds=3.2)
    rows = await asyncio.gather(*(one(discovery, case) for case in CASES))
    errors = [row["query"] for row in rows if not row.get("ok")]
    result = {
        "format": "olya-search-quality-live-probe-v2",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "cases": rows,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
