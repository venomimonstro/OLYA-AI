from __future__ import annotations

import json
import sys

from app.services.local_business_catalog import catalog_status, search_local_catalog


def main() -> int:
    query = ' '.join(sys.argv[1:]).strip() or 'лучшие автосервисы в москве'
    status = catalog_status()
    print('catalog:', json.dumps(status, ensure_ascii=False))
    print('query:', query)
    rows = search_local_catalog(query, limit=10)
    print('rows:', len(rows))
    for index, row in enumerate(rows, 1):
        print(f'{index}. {row.name}')
        print('   card:', row.card_url)
        print('   address:', row.address or '-')
        print('   phone:', row.phone or '-')
        print('   website:', row.website or '-')
    return 0 if rows else 2


if __name__ == '__main__':
    raise SystemExit(main())
