from __future__ import annotations

import argparse
import asyncio
from time import perf_counter

from app.core.config import get_settings
from app.services.discovery import BraveSearchDiscovery, ProviderPoolDiscovery
from app.services.local_business_search import is_local_business_question, resolve_local_business
from app.services.local_search_discovery import LocalSearchDiscovery
from app.services.searxng_discovery import SearxngDiscovery


def _discovery(*, offline: bool):
    providers: list[object] = [LocalSearchDiscovery()]
    if offline:
        return ProviderPoolDiscovery(providers)
    settings = get_settings()
    configured = [
        item.strip().lower()
        for item in (settings.search_providers or settings.search_provider).split(",")
        if item.strip()
    ]
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
    return ProviderPoolDiscovery(providers)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Check the exact OLYA local-business resolver path.")
    parser.add_argument("query", nargs="*", default=[])
    parser.add_argument("--offline", action="store_true", help="Use only OLYA's owned local index; no external providers")
    args = parser.parse_args()
    query = " ".join(args.query).strip() or "лучшие автосервисы в москве"
    print("query:", query)
    print("offline:", args.offline)
    intent = is_local_business_question(query)
    print("local_business_intent:", intent)
    if not intent:
        print("ERROR: query did not enter local-business routing")
        return 3

    started = perf_counter()
    result = await resolve_local_business(
        query,
        _discovery(offline=args.offline),
        allow_external=not args.offline,
    )
    elapsed = perf_counter() - started
    print(f"elapsed: {elapsed:.3f}s")
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
