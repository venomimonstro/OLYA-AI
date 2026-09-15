from __future__ import annotations

import argparse
import asyncio
import json

from app.services.local_search_store import get_local_search_store
from app.services.local_web_crawler import LocalWebCrawler


def _same_city(left: str, right: str) -> bool:
    def norm(value: str) -> str:
        return ' '.join(str(value or '').casefold().replace('ё', 'е').split())
    return not right or norm(left) == norm(right)


async def _main() -> int:
    parser = argparse.ArgumentParser(description='OLYA local text search/index maintenance')
    sub = parser.add_subparsers(dest='command', required=True)

    seed = sub.add_parser('seed', help='Discover URLs from robots.txt and sitemap.xml')
    seed.add_argument('domain_or_url')
    seed.add_argument('--max-sitemaps', type=int, default=20)
    seed.add_argument('--max-urls', type=int, default=5000)

    crawl = sub.add_parser('crawl', help='Fetch queued HTML pages and index clean text')
    crawl.add_argument('--limit', type=int, default=20)
    crawl.add_argument('--concurrency', type=int, default=3)

    search = sub.add_parser('search', help='Search the local web index')
    search.add_argument('query')
    search.add_argument('--limit', type=int, default=8)
    search.add_argument('--domain', action='append', default=[])

    business = sub.add_parser('business', help='Search the local business index')
    business.add_argument('query')
    business.add_argument('--city', default='')
    business.add_argument('--category', default='')
    business.add_argument('--limit', type=int, default=10)

    sub.add_parser('stats', help='Show local search index statistics')
    args = parser.parse_args()
    store = get_local_search_store()

    if args.command == 'seed':
        result = await LocalWebCrawler().seed_domain(
            args.domain_or_url,
            max_sitemaps=max(1, min(args.max_sitemaps, 200)),
            max_urls=max(1, min(args.max_urls, 100_000)),
        )
    elif args.command == 'crawl':
        result = await LocalWebCrawler().crawl_batch(
            limit=max(1, min(args.limit, 500)),
            concurrency=max(1, min(args.concurrency, 8)),
        )
    elif args.command == 'search':
        rows = store.search_pages(args.query, domains=args.domain, limit=args.limit)
        result = [
            {
                'title': row.title,
                'url': row.url,
                'domain': row.domain,
                'description': row.description,
                'fetched_at': row.fetched_at,
                'score': row.score,
                'snippet': row.content[:500],
            }
            for row in rows
        ]
    elif args.command == 'business':
        fetch_limit = max(50, min(int(args.limit) * 8, 500))
        # Canonical category is stronger than free-text morphology. Do not pass
        # Cyrillic city through SQLite NOCASE; filter it with Python casefold.
        rows = store.search_businesses(
            '' if args.category else args.query,
            category=args.category,
            limit=fetch_limit,
        )
        if args.query and not args.category:
            rows = store.search_businesses(args.query, limit=fetch_limit)
        rows = [row for row in rows if _same_city(row.city, args.city)][:max(1, min(int(args.limit), 100))]
        result = [
            {
                'name': row.name,
                'category': row.category,
                'city': row.city,
                'address': row.address,
                'phone': row.phone,
                'website': row.website,
                'source': row.source,
                'source_url': row.source_url,
                'updated_at': row.updated_at,
                'score': row.score,
            }
            for row in rows
        ]
    else:
        result = store.stats()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(_main()))
