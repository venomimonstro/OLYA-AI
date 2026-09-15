from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.services.local_search_store import get_local_search_store
from app.services.local_web_crawler import LocalWebCrawler


async def cycle(*, seeds_file: str, seed_limit: int, max_urls: int, crawl_limit: int, concurrency: int, refresh: bool) -> dict:
    crawler = LocalWebCrawler(); store = get_local_search_store(); before = store.stats(); seeded = []
    seeds = Path(seeds_file)
    if seeds.is_file() and (refresh or int(before.get('queued') or 0) < max(20, crawl_limit // 2)):
        values = [line.strip() for line in seeds.read_text(encoding='utf-8', errors='replace').splitlines()]
        values = [value for value in values if value and not value.startswith('#')][:seed_limit]
        for value in values:
            try: seeded.append(await crawler.seed_domain(value, max_urls=max_urls))
            except Exception as exc: seeded.append({'domain':value,'error':f'{type(exc).__name__}: {exc}'})
    crawled = await crawler.crawl_batch(limit=crawl_limit, concurrency=concurrency)
    return {'before':before,'seeded':seeded,'crawl':crawled,'stats':store.stats()}


async def main_async() -> int:
    parser = argparse.ArgumentParser(description='Low-RAM OLYA local search background worker / cron job.')
    parser.add_argument('--seeds-file', default='/app/data/search-seeds.txt')
    parser.add_argument('--seed-limit', type=int, default=20)
    parser.add_argument('--max-urls', type=int, default=3000)
    parser.add_argument('--crawl-limit', type=int, default=60)
    parser.add_argument('--concurrency', type=int, default=2)
    parser.add_argument('--interval', type=int, default=0, help='0 = one-shot; otherwise repeat every N seconds')
    parser.add_argument('--refresh-seeds', action='store_true')
    args = parser.parse_args()
    while True:
        result = await cycle(
            seeds_file=args.seeds_file, seed_limit=max(0,args.seed_limit), max_urls=max(1,args.max_urls),
            crawl_limit=max(1,min(args.crawl_limit,500)), concurrency=max(1,min(args.concurrency,4)), refresh=args.refresh_seeds,
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.interval <= 0: return 0
        await asyncio.sleep(max(60,args.interval))


def main() -> int: return asyncio.run(main_async())


if __name__ == '__main__': raise SystemExit(main())
