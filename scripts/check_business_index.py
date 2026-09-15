from __future__ import annotations

import sys

from app.services.business_local_index import index_stats, load_places, query_scope


def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "лучшие автосервисы в москве"
    city, category = query_scope(query)
    rows = load_places(query, limit=20)
    print("query:", query)
    print("city:", city)
    print("category:", category or "none")
    print("index:", index_stats())
    print("matches:", len(rows))
    for index, row in enumerate(rows[:10], 1):
        print(f"{index}. {row.name}")
        print("   provider:", row.provider)
        print("   card:", row.card_url)
        print("   address:", row.address or "-")
        print("   phone:", row.phone or "-")
        print("   website:", row.website or "-")
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
