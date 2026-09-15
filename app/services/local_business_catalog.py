from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from app.services.public_maps_discovery import MapPlace
from app.services.response_strategy import normalized_question


_DEFAULT_PATH = Path('/app/data/local_businesses.sqlite3')
_WORD = re.compile(r'[a-zа-яё0-9]+', re.I)

_CITY_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'\bмоскв\w*\b', re.I), 'Москва'),
    (re.compile(r'\b(?:санкт[-\s]?петербург\w*|петербург\w*|спб)\b', re.I), 'Санкт-Петербург'),
    (re.compile(r'\bказан\w*\b', re.I), 'Казань'),
    (re.compile(r'\bекатеринбург\w*\b', re.I), 'Екатеринбург'),
    (re.compile(r'\bновосибирск\w*\b', re.I), 'Новосибирск'),
    (re.compile(r'\bсамар\w*\b', re.I), 'Самара'),
    (re.compile(r'\bчелябинск\w*\b', re.I), 'Челябинск'),
    (re.compile(r'\bкрасноярск\w*\b', re.I), 'Красноярск'),
    (re.compile(r'\bтюмен\w*\b', re.I), 'Тюмень'),
    (re.compile(r'\bуф\w*\b', re.I), 'Уфа'),
    (re.compile(r'\bперм\w*\b', re.I), 'Пермь'),
    (re.compile(r'\bсочи\b', re.I), 'Сочи'),
    (re.compile(r'\bкалининград\w*\b', re.I), 'Калининград'),
    (re.compile(r'\bворонеж\w*\b', re.I), 'Воронеж'),
    (re.compile(r'\bкраснодар\w*\b', re.I), 'Краснодар'),
    (re.compile(r'\bомск\w*\b', re.I), 'Омск'),
    (re.compile(r'\bнижн\w*\s+новгород\w*\b', re.I), 'Нижний Новгород'),
    (re.compile(r'\bростов\w*(?:-на-дону)?\b', re.I), 'Ростов-на-Дону'),
)

_CATEGORY_RULES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r'автосервис|авторемонт|ремонт\w*\s+(?:авто|машин)|сто\b', re.I), ('car_repair',)),
    (re.compile(r'шиномонтаж|шины|покрышк', re.I), ('tyres',)),
    (re.compile(r'автомойк', re.I), ('car_wash',)),
    (re.compile(r'слухопротез|слухов\w*\s+аппарат|сурдолог|аудиолог', re.I), ('hearing_aids', 'audiologist')),
    (re.compile(r'стоматолог', re.I), ('dentist',)),
    (re.compile(r'клиник|медицин\w*\s+центр|диагност\w*\s+центр', re.I), ('clinic', 'doctors')),
    (re.compile(r'аптек', re.I), ('pharmacy',)),
    (re.compile(r'ветеринар|ветклиник', re.I), ('veterinary',)),
    (re.compile(r'юрист|адвокат|юридичес', re.I), ('lawyer',)),
    (re.compile(r'нотариус', re.I), ('notary',)),
    (re.compile(r'риелтор|риэлтор|недвижим', re.I), ('estate_agent',)),
    (re.compile(r'страхов', re.I), ('insurance',)),
    (re.compile(r'банк\w*', re.I), ('bank',)),
    (re.compile(r'ресторан', re.I), ('restaurant',)),
    (re.compile(r'кафе|кофейн', re.I), ('cafe',)),
    (re.compile(r'бар\b', re.I), ('bar', 'pub')),
    (re.compile(r'фитнес|спортзал|тренаж', re.I), ('fitness_centre',)),
    (re.compile(r'салон\w*\s+красот|косметолог|маникюр|педикюр', re.I), ('beauty',)),
    (re.compile(r'парикмах|барбершоп', re.I), ('hairdresser',)),
    (re.compile(r'химчист', re.I), ('dry_cleaning',)),
    (re.compile(r'прачеч', re.I), ('laundry',)),
    (re.compile(r'ателье|портн', re.I), ('tailor',)),
    (re.compile(r'типограф|копицентр', re.I), ('copyshop', 'printer')),
    (re.compile(r'цветоч|цветы', re.I), ('florist',)),
    (re.compile(r'мебел', re.I), ('furniture',)),
    (re.compile(r'пекар', re.I), ('bakery',)),
    (re.compile(r'отел|гостиниц', re.I), ('hotel', 'hostel')),
    (re.compile(r'автошкол', re.I), ('driving_school',)),
    (re.compile(r'языков\w*\s+школ|английск\w*\s+школ', re.I), ('language_school',)),
    (re.compile(r'школ\w*', re.I), ('school',)),
)

_STOP = {
    'лучший', 'лучшие', 'лучших', 'топ', 'рейтинг', 'найди', 'найти', 'подбери', 'посоветуй', 'порекомендуй',
    'покажи', 'хороший', 'хорошие', 'в', 'во', 'на', 'рядом', 'поблизости', 'около', 'отзывы', 'отзыв',
    'москва', 'москве', 'москвы', 'санкт', 'петербург', 'спб', 'компания', 'компании', 'компаний',
}


def requested_city(question: str) -> str:
    text = normalized_question(question)
    for pattern, city in _CITY_ALIASES:
        if pattern.search(text):
            return city
    return ''


def requested_categories(question: str) -> tuple[str, ...]:
    text = normalized_question(question)
    for pattern, values in _CATEGORY_RULES:
        if pattern.search(text):
            return values
    return ()


def _keywords(question: str) -> tuple[str, ...]:
    result: list[str] = []
    for token in _WORD.findall(normalized_question(question)):
        low = token.casefold()
        if len(low) < 4 or low in _STOP or low.isdigit():
            continue
        stem = low[:7]
        if stem not in result:
            result.append(stem)
    return tuple(result[:4])


def _score_row(row: sqlite3.Row, *, categories: tuple[str, ...], keywords: tuple[str, ...]) -> float:
    category = str(row['category'] or '').casefold()
    name = str(row['name'] or '').casefold()
    tags = str(row['search_text'] or '').casefold()
    score = 0.0
    if categories and category in categories:
        score += 10.0
    for keyword in keywords:
        if keyword in name:
            score += 4.0
        elif keyword in tags:
            score += 2.0
    if row['phone']:
        score += 0.8
    if row['website']:
        score += 0.8
    if row['address']:
        score += 0.6
    return score


def search_local_catalog(question: str, *, path: Path | str = _DEFAULT_PATH, limit: int = 12) -> list[MapPlace]:
    db_path = Path(path)
    if not db_path.is_file() or db_path.stat().st_size < 4096:
        return []
    city = requested_city(question)
    if not city:
        return []
    categories = requested_categories(question)
    keywords = _keywords(question)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        params: list[object] = [city]
        where = ['city = ?']
        if categories:
            placeholders = ','.join('?' for _ in categories)
            where.append(f'category IN ({placeholders})')
            params.extend(categories)
        elif keywords:
            keyword_clauses: list[str] = []
            for keyword in keywords:
                keyword_clauses.append('(name_norm LIKE ? OR search_text LIKE ?)')
                params.extend((f'%{keyword}%', f'%{keyword}%'))
            where.append('(' + ' OR '.join(keyword_clauses) + ')')
        query = (
            'SELECT osm_type, osm_id, name, category, address, phone, website, search_text '
            'FROM businesses WHERE ' + ' AND '.join(where) + ' LIMIT 250'
        )
        rows = list(connection.execute(query, params))
    except sqlite3.Error:
        return []
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    ranked = sorted(rows, key=lambda row: _score_row(row, categories=categories, keywords=keywords), reverse=True)
    result: list[MapPlace] = []
    seen: set[str] = set()
    for row in ranked:
        name = ' '.join(str(row['name'] or '').split()).strip()
        if len(name) < 2:
            continue
        osm_type = str(row['osm_type'] or '')
        osm_id = str(row['osm_id'] or '')
        if osm_type not in {'node', 'way', 'relation'} or not osm_id.isdigit():
            continue
        card_url = f'https://www.openstreetmap.org/{osm_type}/{osm_id}'
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        website = str(row['website'] or '').strip()
        if website and not urlsplit(website).scheme:
            website = 'https://' + website.lstrip('/')
        # Provider stays "osm" so the existing entity renderer treats the
        # persistent snapshot exactly like live OSM evidence, while runtime I/O
        # remains fully local.
        result.append(MapPlace(
            provider='osm',
            name=name[:140],
            card_url=card_url,
            address=str(row['address'] or '')[:220],
            phone=str(row['phone'] or '')[:80],
            website=website[:500],
            source_url=card_url,
        ))
        if len(result) >= max(1, limit):
            break
    return result


def catalog_status(*, path: Path | str = _DEFAULT_PATH) -> dict[str, object]:
    db_path = Path(path)
    if not db_path.is_file():
        return {'ready': False, 'path': str(db_path), 'businesses': 0, 'cities': 0, 'updated_at': ''}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=1.0)
        businesses = int(connection.execute('SELECT COUNT(*) FROM businesses').fetchone()[0])
        cities = int(connection.execute('SELECT COUNT(DISTINCT city) FROM businesses').fetchone()[0])
        row = connection.execute("SELECT value FROM metadata WHERE key='updated_at'").fetchone()
        updated_at = str(row[0]) if row else ''
    except sqlite3.Error:
        return {'ready': False, 'path': str(db_path), 'businesses': 0, 'cities': 0, 'updated_at': ''}
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    return {'ready': businesses > 0, 'path': str(db_path), 'businesses': businesses, 'cities': cities, 'updated_at': updated_at}
