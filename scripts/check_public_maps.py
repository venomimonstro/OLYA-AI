from __future__ import annotations

import asyncio
import sys
from time import perf_counter

from app.services.public_maps_discovery import PublicMapsDiscovery


async def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "лучшие центры слухопротезирования в москве"
    started = perf_counter()
    discovery = PublicMapsDiscovery(timeout_seconds=5.0)
    rows = await discovery.search(query)
    elapsed = perf_counter() - started

    print(f"query: {query}")
    print(f"elapsed: {elapsed:.2f}s")
    print(f"cards: {len(rows)}")

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

    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
