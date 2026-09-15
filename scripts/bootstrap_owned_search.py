from __future__ import annotations

import argparse
import json
import subprocess
import sys

from app.services.local_search_store import get_local_search_store


def _run(args: list[str]) -> int:
    print('+', ' '.join(args), file=sys.stderr, flush=True)
    return subprocess.run(args, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description='One-command bootstrap for OLYA owned geo + web search.')
    parser.add_argument('--district', action='append', default=[], help='Geofabrik federal district; may be repeated')
    parser.add_argument('--all-russia', action='store_true', help='Sequentially index all Russia federal districts')
    parser.add_argument('--central', action='store_true', help='Use the full Central Federal District instead of the fast Moscow city extract')
    parser.add_argument('--website-limit', type=int, default=100)
    parser.add_argument('--crawl-limit', type=int, default=100)
    parser.add_argument('--crawl-concurrency', type=int, default=1)
    parser.add_argument('--skip-web', action='store_true')
    args = parser.parse_args()

    python = sys.executable
    if args.all_russia or args.central or args.district:
        geo = [python, '-m', 'scripts.bootstrap_russia_geo_index']
        if args.all_russia:
            geo.append('--all-russia')
        elif args.central and not args.district:
            geo += ['--district', 'central']
        else:
            for district in list(dict.fromkeys(args.district)):
                geo += ['--district', district]
    else:
        # The ordinary first installation should become useful quickly. BBBike's
        # Moscow extract is far smaller than the full Central Federal District;
        # operators can expand to --all-russia after the chat is already usable.
        geo = [python, '-m', 'scripts.bootstrap_moscow_owned_search']

    if _run(geo) != 0:
        print(json.dumps({'ok':False,'stage':'geo','stats':get_local_search_store().stats()}, ensure_ascii=False, indent=2))
        return 2

    if not args.skip_web:
        if _run([
            python, '-m', 'scripts.seed_business_websites',
            '--limit', str(max(1,min(args.website_limit,1000))),
            '--max-urls-per-domain', '250',
        ]) != 0:
            print(json.dumps({'ok':False,'stage':'seed_web','stats':get_local_search_store().stats()}, ensure_ascii=False, indent=2))
            return 3
        if _run([
            python, '-m', 'scripts.local_search_crawler', 'crawl',
            '--limit', str(max(1,min(args.crawl_limit,500))),
            '--concurrency', str(max(1,min(args.crawl_concurrency,2))),
        ]) != 0:
            print(json.dumps({'ok':False,'stage':'crawl','stats':get_local_search_store().stats()}, ensure_ascii=False, indent=2))
            return 4

    stats = get_local_search_store().stats()
    ok = int(stats.get('businesses') or 0) > 0
    print(json.dumps({'ok':ok,'stats':stats}, ensure_ascii=False, indent=2))
    return 0 if ok else 5


if __name__ == '__main__':
    raise SystemExit(main())
