from __future__ import annotations

import asyncio
import sys
from collections import Counter
from time import perf_counter

from app.services.russian_maps_discovery import RussianMapsDiscovery
from app.services.yell_discovery import discover_yell
from app.services.zoon_discovery import discover_zoon
from app.services.searxng_discovery import SearxngDiscovery


async def timed(name: str, coro):
    started = perf_counter()
    try:
        rows = await coro
        return name, rows, perf_counter() - started, ""
    except Exception as exc:
        return name, [], perf_counter() - started, f"{type(exc).__name__}: {exc}"


async def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "лучшие автосервисы в москве"
    discovery = SearxngDiscovery("http://searxng:8080", timeout_seconds=5.0)
    maps = RussianMapsDiscovery(timeout_seconds=4.0)

    results = await asyncio.gather(
        timed("yandex_2gis", maps.search(query)),
        timed("zoon", discover_zoon(query, discovery, limit=8)),
        timed("yell", discover_yell(query, limit=8)),
    )

    print("query:", query)
    total = 0
    for name, rows, elapsed, error in results:
        total += len(rows)
        print("\n" + "=" * 72)
        print("provider:", name)
        print("elapsed:", f"{elapsed:.2f}s")
        print("rows:", len(rows))
        print("error:", error or "none")
        print("kinds:", dict(Counter(getattr(row, "provider", "unknown") for row in rows)) or "none")
        for index, row in enumerate(rows[:8], 1):
            print(f"{index}. {row.name}")
            print("   card:", row.card_url)
            print("   address:", row.address or "-")
            print("   rating:", row.rating if row.rating is not None else "-", "reviews:", row.reviews if row.reviews is not None else "-")
            print("   phone:", row.phone or "-")

    print("\ntotal:", total)
    return 0 if total else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
