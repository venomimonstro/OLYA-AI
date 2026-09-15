from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from urllib.parse import urlsplit

from app.services.local_search_store import get_local_search_store
from app.services.local_web_crawler import LocalWebCrawler


async def main_async() -> int:
    parser = argparse.ArgumentParser(description='Discover robots/sitemaps for official websites already present in the local business index.')
    parser.add_argument('--limit', type=int, default=1000, help='Maximum unique business domains to inspect')
    parser.add_argument('--city', default='')
    parser.add_argument('--max-urls-per-domain', type=int, default=300)
    parser.add_argument('--max-sitemaps', type=int, default=20)
    args = parser.parse_args()

    store = get_local_search_store(); store.ensure_schema()
    clauses = ["website!=''"]; params: list[object] = []
    if args.city.strip():
        # SQLite lower() is ASCII-only; fetch a wider set and apply Unicode city filtering in Python below.
        pass
    sql = f"SELECT website, source_url, city FROM businesses WHERE {' AND '.join(clauses)} ORDER BY confidence DESC, updated_at DESC LIMIT ?"
    params.append(max(1, min(args.limit * 8, 100_000)))
    connection = sqlite3.connect(str(store.path)); connection.row_factory = sqlite3.Row
    try: rows = connection.execute(sql, params).fetchall()
    finally: connection.close()

    wanted_city = args.city.strip().casefold().replace('ё','е')
    roots: list[str] = []; seen_domains: set[str] = set(); skipped = 0
    for row in rows:
        if wanted_city and str(row['city'] or '').casefold().replace('ё','е') != wanted_city:
            continue
        raw = str(row['website'] or '').strip()
        if raw.startswith('www.'): raw = 'https://' + raw
        parsed = urlsplit(raw)
        host = (parsed.hostname or '').casefold().removeprefix('www.')
        if parsed.scheme not in {'http','https'} or not host or host in seen_domains:
            skipped += 1; continue
        seen_domains.add(host); roots.append(f'{parsed.scheme}://{parsed.netloc}/')
        if len(roots) >= max(1,args.limit): break

    crawler = LocalWebCrawler(); seeded = []; failed = 0
    # Deliberately sequential: sitemap discovery is background maintenance and
    # must not create a connection storm or compete with the local LLM.
    for root in roots:
        try:
            seeded.append(await crawler.seed_domain(
                root,
                max_sitemaps=max(1,min(args.max_sitemaps,100)),
                max_urls=max(1,min(args.max_urls_per_domain,10_000)),
            ))
        except Exception as exc:
            failed += 1
            seeded.append({'domain':root,'error':f'{type(exc).__name__}: {exc}'})

    print(json.dumps({
        'domains':len(roots), 'failed_domains':failed, 'skipped':skipped,
        'queued_from_sitemaps':sum(int(item.get('queued') or 0) for item in seeded if isinstance(item,dict)),
        'details':seeded[:100], 'stats':store.stats(),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int: return asyncio.run(main_async())


if __name__ == '__main__': raise SystemExit(main())
