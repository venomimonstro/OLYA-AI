#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from time import perf_counter
from urllib.parse import urlsplit

from app.services.searxng_discovery import SearxngDiscovery


QUERY = "current President of the United States site:whitehouse.gov"


async def probe() -> dict:
    base = os.getenv("X1_SEARXNG_BASE_URL", "http://searxng:8080").rstrip("/")
    client = SearxngDiscovery(base, timeout_seconds=8.0)
    errors: list[str] = []
    hits = []
    started = perf_counter()
    try:
        hits = await client.search(QUERY, count=8, country="US", language="en")
    except Exception as exc:
        errors.append(f"search_request_failed:{exc.__class__.__name__}")
    elapsed_ms = max(0, int((perf_counter() - started) * 1000))

    observed: set[str] = set()
    examples: list[dict] = []
    official_hit = False
    current_holder_signal = False
    for hit in hits:
        provider = str(hit.provider or "")
        if provider.startswith("searxng:"):
            observed.update(item for item in provider.split(":", 1)[1].split(",") if item)
        host = (urlsplit(hit.url).hostname or "").lower()
        text = f"{hit.title} {hit.snippet}".casefold()
        if host.endswith("whitehouse.gov") and urlsplit(hit.url).path.lower().startswith("/administration"):
            official_hit = True
        if "donald" in text and "trump" in text:
            current_holder_signal = True
        if len(examples) < 6:
            examples.append({
                "title": hit.title[:120],
                "url": hit.url,
                "provider": provider,
            })

    if not hits:
        errors.append("no_search_results")
    if len(observed) < 2:
        errors.append("fewer_than_two_search_engines_observed")
    if not official_hit:
        errors.append("whitehouse_not_present_in_metasearch_results")
    if not current_holder_signal:
        errors.append("current_holder_not_visible_in_search_results")
    if elapsed_ms > 7000:
        errors.append("interactive_search_too_slow")

    return {
        "format": "olya-current-search-live-v2",
        "status": "passed" if not errors else "degraded",
        "errors": errors,
        "query": QUERY,
        "elapsed_ms": elapsed_ms,
        "requested_engines": list(client.general_engines),
        "observed_engines": sorted(observed),
        "result_count": len(hits),
        "official_whitehouse_result": official_hit,
        "current_holder_signal": current_holder_signal,
        "examples": examples,
    }


def main() -> int:
    result = asyncio.run(probe())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
