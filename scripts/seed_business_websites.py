from __future__ import annotations

import argparse
import json
import sqlite3
from urllib.parse import urlsplit

from app.services.local_search_store import get_local_search_store


def main() -> int:
    parser = argparse.ArgumentParser(description='Seed OLYA web crawler from official websites already present in the local business index.')
    parser.add_argument('--limit', type=int, default=5000)
    parser.add_argument('--city', default='')
    parser.add_argument('--priority', type=int, default=60)
    args = parser.parse_args()

    store = get_local_search_store(); store.ensure_schema()
    clauses = ["website!=''"]; params: list[object] = []
    if args.city.strip():
        clauses.append('lower(city) LIKE ?'); params.append('%' + args.city.strip().casefold() + '%')
    sql = f"SELECT website, source_url FROM businesses WHERE {' AND '.join(clauses)} ORDER BY confidence DESC, updated_at DESC LIMIT ?"
    params.append(max(1, min(args.limit, 100_000)))
    connection = sqlite3.connect(str(store.path)); connection.row_factory = sqlite3.Row
    try: rows = connection.execute(sql, params).fetchall()
    finally: connection.close()

    seeded = skipped = 0; seen_domains: set[str] = set()
    for row in rows:
        raw = str(row['website'] or '').strip()
        if raw.startswith('www.'): raw = 'https://' + raw
        parsed = urlsplit(raw)
        host = (parsed.hostname or '').casefold().removeprefix('www.')
        if parsed.scheme not in {'http','https'} or not host or host in seen_domains:
            skipped += 1; continue
        seen_domains.add(host)
        root = f'{parsed.scheme}://{parsed.netloc}/'
        store.enqueue(root, discovered_from=str(row['source_url'] or ''), priority=max(1,min(args.priority,999)), next_fetch_at='')
        seeded += 1
    print(json.dumps({'seeded_domains':seeded,'skipped':skipped,'stats':store.stats()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__': raise SystemExit(main())
