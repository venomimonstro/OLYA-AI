from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.services.local_search_store import get_local_search_store
from app.services.local_web_crawler import LocalWebCrawler


async def main_async() -> int:
    parser = argparse.ArgumentParser(description='One-shot low-RAM maintenance for OLYA local search')
    parser.add_argument('--seeds-file', default='/app/data/search-seeds.txt')
    parser.add_argument('--seed-limit', type=int, default=10)
    parser.add_argument('--max-urls-per-seed', type=int, default=2000)
    parser.add_argument('--crawl-limit', type=int, default=50)
    parser.add_argument('--concurrency', type=int, default=2)
    parser.add_argument('--refresh-seeds', action='store_true', help='Re-read sitemaps even when crawl queue is not empty')
    args = parser.parse_args()

    crawler = LocalWebCrawler()
    store = get_local_search_store()
    before = store.stats()
    seeds_path = Path(args.seeds_file)
    seeded: list[dict] = []

    should_seed = args.refresh_seeds or int(before.get('queued') or 0) == 0
    if should_seed and seeds_path.is_file():
        values = []
        for raw in seeds_path.read_text(encoding='utf-8', errors='replace').splitlines():
            value = raw.strip()
            if not value or value.startswith('#'):
                continue
            values.append(value)
        for seed in values[:max(0, args.seed_limit)]:
            try:
                seeded.append(await crawler.seed_domain(seed, max_urls=max(1, args.max_urls_per_seed)))
            except Exception as exc:
                seeded.append({'domain': seed, 'error': f'{type(exc).__name__}: {exc}'})

    crawled = await crawler.crawl_batch(
        limit=max(1, min(args.crawl_limit, 500)),
        concurrency=max(1, min(args.concurrency, 4)),
    )
    result = {'seeded': seeded, 'crawl': crawled, 'before': before, 'stats': store.stats()}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == '__main__':
    raise SystemExit(main())
