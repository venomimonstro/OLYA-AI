#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from time import perf_counter
from urllib.parse import urlsplit

from app.services.searxng_discovery import SearxngDiscovery

QUERIES = (
    ("president", "current President of the United States site:whitehouse.gov", "US", "en"),
    ("usd_rub", "курс доллара к рублю сейчас USD RUB", "RU", "ru"),
)


async def one(client: SearxngDiscovery, name: str, query: str, country: str, language: str) -> dict:
    started = perf_counter()
    errors: list[str] = []
    hits = []
    try:
        hits = await client.search(query, count=8, country=country, language=language)
    except Exception as exc:
        errors.append(f"search_request_failed:{exc.__class__.__name__}")
    elapsed_ms = max(0, int((perf_counter() - started) * 1000))

    observed: set[str] = set()
    hosts: set[str] = set()
    examples: list[dict] = []
    for hit in hits:
        provider = str(hit.provider or "")
        if provider.startswith("searxng:"):
            observed.update(item for item in provider.split(":", 1)[1].split(",") if item)
        host = (urlsplit(hit.url).hostname or "").lower().removeprefix("www.")
        if host:
            hosts.add(host)
        if len(examples) < 5:
            examples.append({"title": hit.title[:120], "url": hit.url, "provider": provider})

    if not hits:
        errors.append("no_search_results")
    if len(observed) < 2:
        errors.append("fewer_than_two_search_engines_observed")
    if len(hosts) < 2:
        errors.append("fewer_than_two_result_domains")
    if elapsed_ms > 4500:
        errors.append("interactive_search_too_slow")

    if name == "president":
        official = any(
            (urlsplit(hit.url).hostname or "").lower().endswith("whitehouse.gov")
            and urlsplit(hit.url).path.lower().startswith("/administration")
            for hit in hits
        )
        holder = any("donald" in f"{hit.title} {hit.snippet}".casefold() and "trump" in f"{hit.title} {hit.snippet}".casefold() for hit in hits)
        if not official:
            errors.append("whitehouse_not_present_in_results")
        if not holder:
            errors.append("current_holder_not_visible_in_results")

    if name == "usd_rub":
        text = " ".join(f"{hit.title} {hit.snippet}" for hit in hits).casefold()
        if not any(token in text for token in ("usd", "доллар")) or not any(token in text for token in ("rub", "рубл")):
            errors.append("currency_pair_not_visible_in_results")

    return {
        "name": name,
        "query": query,
        "elapsed_ms": elapsed_ms,
        "observed_engines": sorted(observed),
        "independent_domains": len(hosts),
        "result_count": len(hits),
        "errors": errors,
        "examples": examples,
    }


async def probe() -> dict:
    base = os.getenv("X1_SEARXNG_BASE_URL", "http://searxng:8080").rstrip("/")
    client = SearxngDiscovery(base, timeout_seconds=4.0)
    started = perf_counter()
    rows = await asyncio.gather(*(one(client, *item) for item in QUERIES))
    total_ms = max(0, int((perf_counter() - started) * 1000))
    errors = [f"{row['name']}:{error}" for row in rows for error in row["errors"]]
    return {
        "format": "olya-current-search-live-v3",
        "status": "passed" if not errors else "degraded",
        "errors": errors,
        "total_elapsed_ms": total_ms,
        "requested_engines": list(client.general_engines),
        "queries": rows,
    }


def main() -> int:
    result = asyncio.run(probe())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
