from __future__ import annotations

import argparse
import json

from app.services.local_search_store import get_local_search_store
from scripts.migrate_local_search_index import migrate_catalog, migrate_live_cache
from scripts.sync_local_business_catalog import CATALOG_PATH, DATA_ROOT, sync


def main() -> int:
    parser = argparse.ArgumentParser(description='Bootstrap OLYA local geo/search core with compact OSM city extracts')
    parser.add_argument('--city', action='append', dest='cities', help='Configured city; may be repeated')
    parser.add_argument('--all-configured', action='store_true')
    parser.add_argument('--force-download', action='store_true')
    parser.add_argument('--migrate-only', action='store_true', help='Do not download OSM; migrate existing local databases only')
    args = parser.parse_args()

    sync_result = None
    if not args.migrate_only:
        cities = ['Москва', 'Санкт-Петербург'] if args.all_configured else (args.cities or ['Москва'])
        try:
            sync_result = sync(cities, force_download=args.force_download)
        except Exception as exc:
            print(json.dumps({'ok': False, 'stage': 'osm_sync', 'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False, indent=2))
            return 1

    catalog_rows = migrate_catalog(CATALOG_PATH)
    cache_rows = migrate_live_cache(DATA_ROOT / 'business_index.sqlite3')
    stats = get_local_search_store().stats()
    ok = int(stats.get('businesses') or 0) > 0
    print(json.dumps({
        'ok': ok,
        'sync': sync_result,
        'migrated_catalog_rows': catalog_rows,
        'migrated_live_cache_rows': cache_rows,
        'search_core': stats,
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
