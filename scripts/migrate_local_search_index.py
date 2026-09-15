from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from app.services.local_search_store import get_local_search_store


DATA_ROOT = Path('/app/data')

_CATEGORY_MAP = {
    'автосервис': 'car_repair', 'слухопротезирование': 'hearing_aids', 'стоматология': 'dentist',
    'медицина': 'clinic', 'аптеки': 'pharmacy', 'ветеринария': 'veterinary', 'юристы': 'lawyer',
    'недвижимость': 'estate_agent', 'рестораны': 'restaurant', 'кафе': 'cafe', 'фитнес': 'fitness_centre',
    'красота': 'beauty', 'парикмахерские': 'hairdresser', 'шиномонтаж': 'tyres', 'автомойка': 'car_wash',
    'химчистка': 'dry_cleaning', 'гостиницы': 'hotel',
}
_CITY_MAP = {
    'москва': 'Москва', 'санкт-петербург': 'Санкт-Петербург', 'казань': 'Казань', 'екатеринбург': 'Екатеринбург',
    'новосибирск': 'Новосибирск', 'самара': 'Самара', 'челябинск': 'Челябинск', 'красноярск': 'Красноярск',
    'тюмень': 'Тюмень', 'уфа': 'Уфа', 'пермь': 'Пермь', 'сочи': 'Сочи', 'воронеж': 'Воронеж',
    'краснодар': 'Краснодар', 'омск': 'Омск',
}


def _category(value: object) -> str:
    raw = str(value or '').strip()
    return _CATEGORY_MAP.get(raw.casefold(), raw[:120])


def _city(value: object) -> str:
    raw = str(value or '').strip()
    return _CITY_MAP.get(raw.casefold().replace('ё', 'е'), raw[:180])


def migrate_catalog(path: Path) -> int:
    if not path.is_file():
        return 0
    store = get_local_search_store()
    connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    written = 0
    try:
        rows = connection.execute('SELECT city,osm_type,osm_id,name,category,address,phone,website FROM businesses')
        for row in rows:
            osm_type = str(row['osm_type'] or '')
            osm_id = str(row['osm_id'] or '')
            source_id = f'{osm_type}/{osm_id}' if osm_type in {'node', 'way', 'relation'} and osm_id.isdigit() else ''
            source_url = f'https://www.openstreetmap.org/{source_id}' if source_id else ''
            category = _category(row['category'])
            try:
                store.upsert_business({
                    'name': row['name'], 'category': category, 'subcategory': category,
                    'city': _city(row['city']), 'address': row['address'], 'phone': row['phone'], 'website': row['website'],
                    'source': 'osm', 'source_id': source_id, 'source_url': source_url, 'confidence': 0.78,
                })
                written += 1
            except (ValueError, sqlite3.Error):
                continue
    finally:
        connection.close()
    return written


def migrate_live_cache(path: Path) -> int:
    if not path.is_file():
        return 0
    store = get_local_search_store()
    connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    written = 0
    try:
        columns = {str(row[1]) for row in connection.execute('PRAGMA table_info(business_places)')}
        if not columns:
            return 0
        rows = connection.execute('SELECT * FROM business_places')
        for row in rows:
            values = dict(row)
            category = _category(values.get('category', ''))
            try:
                store.upsert_business({
                    'name': values.get('name', ''), 'category': category, 'subcategory': category,
                    'city': _city(values.get('city', '')), 'address': values.get('address', ''),
                    'phone': values.get('phone', ''), 'website': values.get('website', ''),
                    'source': values.get('provider', '') or 'legacy_cache',
                    'source_url': values.get('card_url', '') or values.get('source_url', ''),
                    'source_id': values.get('source_id', ''), 'confidence': 0.62,
                })
                written += 1
            except (ValueError, sqlite3.Error):
                continue
    finally:
        connection.close()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description='Migrate OLYA legacy local search databases into olya_search.db')
    parser.add_argument('--catalog', default=str(DATA_ROOT / 'local_businesses.sqlite3'))
    parser.add_argument('--live-cache', default=str(DATA_ROOT / 'business_index.sqlite3'))
    args = parser.parse_args()
    catalog_rows = migrate_catalog(Path(args.catalog))
    cache_rows = migrate_live_cache(Path(args.live_cache))
    result = {'ok': True, 'catalog_rows': catalog_rows, 'live_cache_rows': cache_rows, 'stats': get_local_search_store().stats()}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
