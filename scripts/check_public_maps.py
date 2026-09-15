from __future__ import annotations

import asyncio
import re
import sys
from time import perf_counter

from app.core.config import get_settings
from app.services.discovery import BraveSearchDiscovery, DisabledDiscovery, ProviderPoolDiscovery
from app.services.russian_maps_discovery import RussianMapsDiscovery
from app.services.searxng_discovery import SearxngDiscovery
from app.services.yandex_medicine_discovery import discover_yandex_medicine
from app.services.yell_discovery import discover_yell
from app.services.zoon_discovery import discover_zoon


_MEDICAL_RE = re.compile(
    r"\b(?:медицин\w*|клиник\w*|стоматолог\w*|врач\w*|доктор\w*|сурдолог\w*|"
    r"слухопротезирован\w*|слухов\w+\s+аппарат\w*|лаборатор\w*|диагност\w*)\b",
    re.I,
)


def _discovery():
    settings = get_settings()
    configured = [item.strip().lower() for item in (settings.search_providers or settings.search_provider).split(",") if item.strip()]
    providers = []
    for name in configured:
        if name == "searxng":
            providers.append(SearxngDiscovery(settings.searxng_base_url, timeout_seconds=settings.search_timeout_seconds))
        elif name == "brave" and settings.brave_search_api_key:
            providers.append(BraveSearchDiscovery(settings.brave_search_api_key, timeout_seconds=settings.search_timeout_seconds))
    return ProviderPoolDiscovery(providers) if providers else DisabledDiscovery()


def _print_rows(label: str, rows) -> None:
    print(f"\n{label}: {len(rows)}")
    grouped: dict[str, int] = {}
    for row in rows:
        grouped[row.provider] = grouped.get(row.provider, 0) + 1
    print("providers:", grouped or "none")
    for index, row in enumerate(rows[:20], start=1):
        print("\n" + "=" * 72)
        print(index, row.provider)
        print("name:", row.name)
        print("card:", row.card_url)
        print("address:", row.address or "-")
        print("phone:", row.phone or "-")
        print("website:", row.website or "-")
        print("rating:", row.rating if row.rating is not None else "-")
        print("reviews:", row.reviews if row.reviews is not None else "-")
        print("source:", row.source_url)


async def _empty():
    return []


async def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "лучшие центры слухопротезирования в москве"
    started = perf_counter()
    maps = RussianMapsDiscovery(timeout_seconds=5.0)
    search = _discovery()

    direct_task = asyncio.create_task(maps.search(query))
    zoon_task = asyncio.create_task(discover_zoon(query, search, limit=10))
    yell_task = asyncio.create_task(discover_yell(query, limit=10))
    medicine_task = asyncio.create_task(
        discover_yandex_medicine(query, search, limit=10) if _MEDICAL_RE.search(query) else _empty()
    )
    direct_rows, zoon_rows, yell_rows, medicine_rows = await asyncio.gather(
        direct_task, zoon_task, yell_task, medicine_task
    )
    elapsed = perf_counter() - started

    print(f"query: {query}")
    print(f"elapsed: {elapsed:.2f}s")
    _print_rows("direct_yandex_2gis", direct_rows)
    _print_rows("zoon", zoon_rows)
    _print_rows("yell", yell_rows)
    _print_rows("yandex_medicine", medicine_rows)

    all_rows = [*direct_rows, *zoon_rows, *yell_rows, *medicine_rows]
    unique = {(row.provider, row.card_url) for row in all_rows}
    print(f"\ntotal_unique_cards: {len(unique)}")
    return 0 if unique else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
