from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DATA_ROOT = Path(os.environ.get('X1_DATA_ROOT', '/app/data'))
CATALOG_PATH = DATA_ROOT / 'local_businesses.sqlite3'
DOWNLOAD_ROOT = DATA_ROOT / 'osm-downloads'

# Compact daily city extracts keep bootstrap cheap enough for the low-memory
# self-hosted deployment. The runtime catalog is independent from the network
# after a successful sync.
CITY_SOURCES = {
    'Москва': {
        'url': 'https://download.bbbike.org/osm/bbbike/Moscow/Moscow.osm.pbf',
        'filename': 'Moscow.osm.pbf',
    },
    'Санкт-Петербург': {
        'url': 'https://download.bbbike.org/osm/bbbike/SanktPetersburg/SanktPetersburg.osm.pbf',
        'filename': 'SanktPetersburg.osm.pbf',
    },
}

BUSINESS_FILTERS = (
    'nwr/shop',
    'nwr/amenity',
    'nwr/office',
    'nwr/craft',
    'nwr/tourism',
    'nwr/leisure',
    'nwr/healthcare',
    'nwr/sport',
)

CATEGORY_KEYS = (
    ('shop',), ('amenity',), ('office',), ('craft',), ('tourism',), ('leisure',), ('healthcare',), ('sport',),
)


def _run(*args: str) -> None:
    try:
        subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or '').strip()[-2000:]
        raise RuntimeError(f"command failed ({' '.join(args[:3])}): {detail}") from exc


def _download(url: str, destination: Path, *, force: bool) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 1_000_000 and not force:
        return destination
    temporary = destination.with_suffix(destination.suffix + '.part')
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'OLYA-AI local catalog sync/1.0'})
    with urllib.request.urlopen(request, timeout=120) as response, temporary.open('wb') as target:
        shutil.copyfileobj(response, target, length=1024 * 1024)
    if temporary.stat().st_size < 1_000_000:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f'downloaded PBF is unexpectedly small: {url}')
    temporary.replace(destination)
    return destination


def _normal(value: object) -> str:
    return ' '.join(str(value or '').casefold().split())[:500]


def _address(properties: dict) -> str:
    full = str(properties.get('addr:full') or '').strip()
    if full:
        return full[:220]
    street = str(properties.get('addr:street') or properties.get('addr:place') or '').strip()
    house = str(properties.get('addr:housenumber') or '').strip()
    district = str(properties.get('addr:district') or '').strip()
    parts = []
    if street:
        parts.append(street + (f', {house}' if house else ''))
    elif house:
        parts.append(house)
    if district:
        parts.append(district)
    return ', '.join(parts)[:220]


def _website(properties: dict) -> str:
    return str(properties.get('contact:website') or properties.get('website') or '').strip()[:500]


def _phone(properties: dict) -> str:
    return str(properties.get('contact:phone') or properties.get('phone') or '').strip()[:80]


def _category(properties: dict) -> str:
    for keys in CATEGORY_KEYS:
        for key in keys:
            value = str(properties.get(key) or '').strip().casefold()
            if value:
                if key == 'healthcare' and value == 'doctor':
                    return 'doctors'
                return value[:80]
    for key in ('cuisine', 'service:vehicle', 'service:vehicle:car_repair'):
        value = str(properties.get(key) or '').strip().casefold()
        if value:
            return value[:80]
    return ''


def _name(properties: dict) -> str:
    for key in ('name', 'brand', 'operator'):
        value = ' '.join(str(properties.get(key) or '').split()).strip()
        if len(value) >= 2:
            return value[:140]
    return ''


def _osm_identity(feature: dict) -> tuple[str, str]:
    props = feature.get('properties') if isinstance(feature, dict) else None
    if not isinstance(props, dict):
        return '', ''
    osm_type = str(props.get('@type') or props.get('type') or '').casefold()
    osm_id = str(props.get('@id') or props.get('id') or '')
    if '/' in osm_id:
        prefix, value = osm_id.split('/', 1)
        osm_type = prefix.casefold()
        osm_id = value
    if osm_type not in {'node', 'way', 'relation'}:
        return '', ''
    if not osm_id.isdigit():
        return '', ''
    return osm_type, osm_id


def _iter_geojsonseq(path: Path):
    with path.open('r', encoding='utf-8', errors='replace') as handle:
        for raw in handle:
            line = raw.strip().lstrip('\x1e')
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def _build_database(city_exports: list[tuple[str, Path]], destination: Path) -> int:
    destination.unlink(missing_ok=True)
    connection = sqlite3.connect(destination)
    try:
        connection.executescript(
            '''
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE businesses (
                city TEXT NOT NULL,
                osm_type TEXT NOT NULL,
                osm_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                name_norm TEXT NOT NULL,
                category TEXT NOT NULL,
                address TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                website TEXT NOT NULL DEFAULT '',
                search_text TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (city, osm_type, osm_id)
            );
            CREATE INDEX idx_businesses_city_category ON businesses(city, category);
            CREATE INDEX idx_businesses_city_name ON businesses(city, name_norm);
            '''
        )
        total = 0
        for city, export_path in city_exports:
            batch = []
            for feature in _iter_geojsonseq(export_path):
                properties = feature.get('properties')
                if not isinstance(properties, dict):
                    continue
                name = _name(properties)
                category = _category(properties)
                osm_type, osm_id = _osm_identity(feature)
                if not name or not category or not osm_type:
                    continue
                address = _address(properties)
                phone = _phone(properties)
                website = _website(properties)
                search_values = [name, category, address]
                for key in ('brand', 'operator', 'description', 'cuisine', 'healthcare:speciality', 'service:vehicle'):
                    if properties.get(key):
                        search_values.append(str(properties[key]))
                batch.append((
                    city, osm_type, int(osm_id), name, _normal(name), category,
                    address, phone, website, _normal(' '.join(search_values)),
                ))
                if len(batch) >= 2000:
                    connection.executemany('INSERT OR REPLACE INTO businesses VALUES (?,?,?,?,?,?,?,?,?,?)', batch)
                    total += len(batch)
                    batch.clear()
            if batch:
                connection.executemany('INSERT OR REPLACE INTO businesses VALUES (?,?,?,?,?,?,?,?,?,?)', batch)
                total += len(batch)
        connection.execute('INSERT INTO metadata(key, value) VALUES (?, ?)', ('updated_at', datetime.now(timezone.utc).isoformat()))
        connection.execute('INSERT INTO metadata(key, value) VALUES (?, ?)', ('schema_version', '1'))
        connection.commit()
        connection.execute('PRAGMA optimize')
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise RuntimeError(f'catalog integrity check failed: {integrity}')
        return total
    finally:
        connection.close()


def sync(cities: list[str], *, force_download: bool = False) -> dict[str, object]:
    if shutil.which('osmium') is None:
        raise RuntimeError('osmium-tool is not installed in the app container')
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    exports: list[tuple[str, Path]] = []
    with tempfile.TemporaryDirectory(prefix='olya-business-', dir=str(DATA_ROOT)) as tmp_raw:
        tmp = Path(tmp_raw)
        for city in cities:
            source = CITY_SOURCES.get(city)
            if source is None:
                raise RuntimeError(f'no compact OSM source configured for city: {city}')
            pbf = _download(source['url'], DOWNLOAD_ROOT / source['filename'], force=force_download)
            filtered = tmp / f'{source["filename"]}.businesses.pbf'
            exported = tmp / f'{source["filename"]}.geojsonseq'
            node_index = tmp / f'{source["filename"]}.nodes.idx'
            _run('osmium', 'tags-filter', str(pbf), *BUSINESS_FILTERS, '-o', str(filtered), '--overwrite')
            # Original OSM identity is required for deterministic card links.
            # sparse_file_array keeps the node-location index on disk instead of
            # consuming the app container's limited RAM during this one-off build.
            _run(
                'osmium', 'export', str(filtered),
                '-f', 'geojsonseq', '-a', 'type,id',
                '-i', f'sparse_file_array,{node_index}',
                '-o', str(exported), '--overwrite',
            )
            exports.append((city, exported))

        staging = DATA_ROOT / 'local_businesses.sqlite3.tmp'
        total = _build_database(exports, staging)
        if total < 100:
            staging.unlink(missing_ok=True)
            raise RuntimeError(f'catalog build returned too few businesses: {total}')
        staging.replace(CATALOG_PATH)
    return {'path': str(CATALOG_PATH), 'businesses': total, 'cities': cities}


def main() -> int:
    parser = argparse.ArgumentParser(description='Build OLYA persistent local-business catalog from OSM extracts.')
    parser.add_argument('--city', action='append', dest='cities', help='City to sync; may be repeated.')
    parser.add_argument('--all-configured', action='store_true', help='Sync all compact city sources configured in this build.')
    parser.add_argument('--force-download', action='store_true', help='Redownload source PBF even when cached.')
    args = parser.parse_args()
    cities = list(CITY_SOURCES) if args.all_configured else (args.cities or ['Москва'])
    unknown = [city for city in cities if city not in CITY_SOURCES]
    if unknown:
        print('unsupported city source:', ', '.join(unknown), file=sys.stderr)
        print('configured:', ', '.join(CITY_SOURCES), file=sys.stderr)
        return 2
    try:
        result = sync(cities, force_download=args.force_download)
    except Exception as exc:
        print(f'catalog sync failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
