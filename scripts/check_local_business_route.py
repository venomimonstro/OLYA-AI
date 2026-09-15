from __future__ import annotations

import asyncio
import sys
from time import perf_counter

from app.core.config import get_settings
from app.services.discovery import BraveSearchDiscovery, DisabledDiscovery, ProviderPoolDiscovery
from app.services.local_business_search import is_local_business_question, resolve_local_business
from app.services.searxng_discovery import SearxngDiscovery


def _discovery():
    settings = get_settings()
    configured = [
        item.strip().lower()
        for item in (settings.search_providers or settings.search_provider).split(",")
        if item.strip()
    ]
    providers = []
    for name in configured:
        if name == "searxng":
            providers.append(
                SearxngDiscovery(
                    settings.searxng_base_url,
                    timeout_seconds=settings.search_timeout_seconds,
                )
            )
        elif name == "brave" and settings.brave_search_api_key:
            providers.append(
                BraveSearchDiscovery(
                    settings.brave_search_api_key,
                    timeout_seconds=settings.search_timeout_seconds,
                )
            )
    return ProviderPoolDiscovery(providers) if providers else DisabledDiscovery()


async def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "лучший сервис москвы"
    print("query:", query)
    intent = is_local_business_question(query)
    print("local_business_intent:", intent)
    if not intent:
        print("ERROR: query did not enter local-business routing")
        return 3

    started = perf_counter()
    result = await resolve_local_business(query, _discovery())
    elapsed = perf_counter() - started
    print(f"elapsed: {elapsed:.2f}s")
    if result is None:
        print("ERROR: resolver returned None")
        return 4

    print("searched:", result.searched)
    print("sources:", len(result.sources))
    providers: dict[str, int] = {}
    for source in result.sources:
        provider = str(source.get("provider") or source.get("domain") or "unknown")
        providers[provider] = providers.get(provider, 0) + 1
    print("providers:", providers or "none")
    print("\n--- answer ---")
    print(result.text)
    return 0 if result.sources else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
