from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from shutil import which

from app.services.local_search_store import get_local_search_store

FILTERS = (
    'nwr/shop', 'nwr/amenity', 'nwr/office', 'nwr/craft',
    'nwr/tourism', 'nwr/leisure', 'nwr/healthcare',
)

CITY_BOXES = (
    ('Москва', 55.45, 56.02, 36.75, 37.98), ('Санкт-Петербург', 59.63, 60.25, 29.40, 30.75),
    ('Казань', 55.60, 55.95, 48.75, 49.45), ('Екатеринбург', 56.65, 57.05, 60.25, 60.95),
    ('Новосибирск', 54.75, 55.25, 82.55, 83.30), ('Самара', 53.05, 53.55, 49.70, 50.45),
    ('Челябинск', 54.95, 55.40, 60.05, 61.00), ('Красноярск', 55.80, 56.25, 92.40, 93.35),
    ('Тюмень', 56.95, 57.35, 65.20, 65.85), ('Уфа', 54.55, 54.95, 55.65, 56.35),
    ('Пермь', 57.80, 58.25, 55.70, 56.65), ('Сочи', 43.35, 44.10, 39.35, 40.20),
    ('Калининград', 54.55, 54.90, 20.25, 20.80), ('Воронеж', 51.45, 51.90, 38.90, 39.55),
    ('Краснодар', 44.85, 45.25, 38.70, 39.35), ('Омск', 54.75, 55.30, 72.75, 73.75),
    ('Нижний Новгород', 56.10, 56.50, 43.55, 44.35), ('Ростов-на-Дону', 47.05, 47.45, 39.35, 40.05),
)


def first_coord(geometry):
    if not isinstance(geometry, dict): return None, None
    found = []
    def walk(value):
        if len(found) >= 128: return
        if isinstance(value, list):
            if len(value) >= 2 and all(isinstance(x, (int, float)) for x in value[:2]):
                found.append((float(value[1]), float(value[0]))); return
            for item in value: walk(item)
    walk(geometry.get('coordinates'))
    if not found: return None, None
    return sum(x for x, _ in found)/len(found), sum(y for _, y in found)/len(found)


def infer_city(tags, lat, lon):
    explicit = str(tags.get('addr:city') or tags.get('addr:place') or tags.get('is_in:city') or '').strip()
    if explicit: return explicit[:180]
    if lat is None or lon is None: return ''
    for city, min_lat, max_lat, min_lon, max_lon in CITY_BOXES:
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon: return city
    return ''


def canonical_category(tags):
    shop = str(tags.get('shop') or '').casefold(); amenity = str(tags.get('amenity') or '').casefold()
    office = str(tags.get('office') or '').casefold(); craft = str(tags.get('craft') or '').casefold()
    tourism = str(tags.get('tourism') or '').casefold(); leisure = str(tags.get('leisure') or '').casefold()
    healthcare = str(tags.get('healthcare') or '').casefold()
    mapping = {
        ('shop','car_repair'):'car_repair', ('amenity','car_repair'):'car_repair', ('shop','tyres'):'tyres',
        ('amenity','car_wash'):'car_wash', ('shop','hearing_aids'):'hearing_aids', ('healthcare','audiologist'):'hearing_aids',
        ('amenity','dentist'):'dentist', ('healthcare','dentist'):'dentist', ('amenity','clinic'):'clinic', ('healthcare','clinic'):'clinic',
        ('amenity','pharmacy'):'pharmacy', ('amenity','veterinary'):'veterinary', ('office','lawyer'):'lawyer', ('office','notary'):'notary',
        ('office','estate_agent'):'estate_agent', ('office','insurance'):'insurance', ('amenity','bank'):'bank',
        ('amenity','restaurant'):'restaurant', ('amenity','cafe'):'cafe', ('amenity','bar'):'bar', ('amenity','pub'):'bar',
        ('leisure','fitness_centre'):'fitness_centre', ('shop','beauty'):'beauty', ('shop','hairdresser'):'hairdresser',
        ('shop','massage'):'massage', ('shop','dry_cleaning'):'dry_cleaning', ('shop','laundry'):'laundry',
        ('craft','tailor'):'tailor', ('shop','copyshop'):'printing', ('craft','printer'):'printing', ('shop','florist'):'florist',
        ('shop','furniture'):'furniture', ('shop','bakery'):'bakery', ('tourism','hotel'):'hotel', ('tourism','hostel'):'hotel',
        ('amenity','driving_school'):'driving_school', ('amenity','language_school'):'language_school',
        ('craft','electrician'):'electrician', ('craft','plumber'):'plumber',
    }
    for namespace, value in (('shop',shop),('amenity',amenity),('office',office),('craft',craft),('tourism',tourism),('leisure',leisure),('healthcare',healthcare)):
        if mapping.get((namespace, value)): return mapping[(namespace, value)], value
    for value in (shop, office, craft, amenity, healthcare, tourism, leisure):
        if value: return value[:120], value[:120]
    return '', ''


def feature_record(feature):
    props = feature.get('properties') if isinstance(feature, dict) else None
    if not isinstance(props, dict): return None
    tags = props.get('tags') if isinstance(props.get('tags'), dict) else props
    name = ' '.join(str(tags.get('name') or tags.get('brand') or tags.get('operator') or '').split()).strip()
    if len(name) < 2: return None
    category, subcategory = canonical_category(tags)
    if not category: return None
    lat, lon = first_coord(feature.get('geometry')); city = infer_city(tags, lat, lon)
    unique = str(props.get('@id') or props.get('id') or '')
    osm_type, osm_id = ('','')
    if '/' in unique: osm_type, osm_id = unique.split('/',1)
    if osm_type not in {'node','way','relation'} or not osm_id.isdigit(): return None
    street = str(tags.get('addr:street') or tags.get('addr:place') or '').strip(); house = str(tags.get('addr:housenumber') or '').strip()
    address = str(tags.get('addr:full') or '').strip() or ', '.join(x for x in ((street + (f', {house}' if house else '')).strip(), city) if x)
    website = str(tags.get('contact:website') or tags.get('website') or '').strip()
    if website.startswith('www.'): website = 'https://' + website
    return {
        'source_key': f'osm:{osm_type}:{osm_id}', 'source':'osm', 'source_id':osm_id,
        'source_url':f'https://www.openstreetmap.org/{osm_type}/{osm_id}', 'name':name,
        'aliases':[tags.get('brand',''), tags.get('operator','')], 'category':category, 'subcategory':subcategory,
        'country':str(tags.get('addr:country') or 'Россия'), 'region':str(tags.get('addr:region') or tags.get('is_in:state') or ''),
        'city':city, 'district':str(tags.get('addr:district') or tags.get('is_in:district') or ''),
        'street':street, 'house':house, 'address':address, 'lat':lat, 'lon':lon,
        'phone':str(tags.get('contact:phone') or tags.get('phone') or ''), 'website':website,
        'opening_hours':str(tags.get('opening_hours') or ''), 'confidence':0.82 if address or website or tags.get('phone') else 0.68,
    }


def import_pbf(path: Path, limit: int = 0):
    if not path.is_file(): raise FileNotFoundError(path)
    if not which('osmium'): raise RuntimeError('osmium-tool is not installed')
    store = get_local_search_store(); written = rejected = 0
    work_root = Path(os.getenv('X1_DATA_ROOT') or '/app/data') / 'search-import'; work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='osm-owned-', dir=str(work_root)) as temp_dir:
        temp = Path(temp_dir); filtered = temp / 'businesses.filtered.osm.pbf'; node_index = temp / 'nodes.idx'
        filter_run = subprocess.run(['osmium','tags-filter',str(path),*FILTERS,'-o',str(filtered),'--overwrite'], capture_output=True, text=True)
        if filter_run.returncode != 0:
            raise RuntimeError('osmium tags-filter failed: ' + filter_run.stderr[-1500:])
        process = subprocess.Popen(
            ['osmium','export',str(filtered),'-f','geojsonseq','-a','type,id','-i',f'sparse_file_array,{node_index}','-o','-'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', bufsize=1,
        )
        assert process.stdout is not None
        try:
            for raw in process.stdout:
                raw = raw.strip().lstrip('\x1e')
                if not raw: continue
                try: record = feature_record(json.loads(raw))
                except Exception: record = None
                if not record: rejected += 1; continue
                try: store.upsert_business(record); written += 1
                except Exception: rejected += 1
                if written and written % 5000 == 0: print(f'indexed={written} rejected={rejected}', file=sys.stderr, flush=True)
                if limit and written >= limit:
                    process.terminate(); break
        finally:
            process.stdout.close()
            try:
                _, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill(); _, stderr = process.communicate()
        if process.returncode not in {0, -15} and not (limit and written >= limit):
            raise RuntimeError(f'osmium export failed ({process.returncode}): {stderr[-1500:]}')
    return written, rejected


def main():
    parser = argparse.ArgumentParser(description='Import any OSM PBF into OLYA SQLite FTS5/RTree search index.')
    parser.add_argument('pbf', nargs='+'); parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    if not which('osmium'): parser.error('osmium-tool is not installed')
    total = rejected = 0
    for value in args.pbf:
        w, r = import_pbf(Path(value), max(0,args.limit)); total += w; rejected += r
        print(f'{value}: indexed={w} rejected={r}')
    print(json.dumps({'indexed':total,'rejected':rejected,'stats':get_local_search_store().stats()}, ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__': raise SystemExit(main())
