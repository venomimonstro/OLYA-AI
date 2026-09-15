from __future__ import annotations

import json
import sys
from time import perf_counter

from app.services.local_business_catalog import catalog_status, search_local_catalog
from app.services.local_search_store import get_local_search_store


def main() -> int:
    query = ' '.join(sys.argv[1:]).strip() or 'лучшие автосервисы в москве'
    store = get_local_search_store()
    started = perf_counter()
    rows = search_local_catalog(query, limit=12)
    elapsed = (perf_counter() - started) * 1000
    print('query:', query)
    print('elapsed_ms:', round(elapsed, 2))
    print('status:', json.dumps(catalog_status(), ensure_ascii=False))
    print('store:', json.dumps(store.stats(), ensure_ascii=False))
    print('rows:', len(rows))
    for index, row in enumerate(rows, 1):
        print(f'{index}. {row.name}')
        print('   provider:', row.provider)
        print('   rating:', row.rating if row.rating is not None else '-')
        print('   reviews:', row.reviews if row.reviews is not None else '-')
        print('   address:', row.address or '-')
        print('   phone:', row.phone or '-')
        print('   website:', row.website or '-')
        print('   card:', row.card_url or '-')
    return 0 if rows else 2


if __name__ == '__main__':
    raise SystemExit(main())
