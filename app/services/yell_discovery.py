from __future__ import annotations

import html
import re
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.services.public_maps_discovery import MapPlace
from app.services.response_strategy import normalized_question


_ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r'<[^>]+>')
_WORD_RE = re.compile(r'[a-zа-яё0-9]+', re.I)
_BLOCK_RE = re.compile(r'captcha|smartcaptcha|access denied|доступ временно ограничен|проверка, что вы не робот', re.I)
_RATING_REVIEWS_RE = re.compile(r'\b([0-5](?:[.,]\d+)?)\s+(\d[\d\s]{0,8})\s+отзыв', re.I)
_RATING_ONLY_RE = re.compile(r'\b([0-5][.,]\d)\b')
_PHONE_RE = re.compile(r'(?:\+7|8)[\s()\-\d]{9,18}')

_CITY_SLUGS = {
    'москва': ('moscow', 'Москва'), 'москве': ('moscow', 'Москва'), 'москвы': ('moscow', 'Москва'),
    'санкт-петербург': ('spb', 'Санкт-Петербург'), 'санкт-петербурге': ('spb', 'Санкт-Петербург'),
    'петербург': ('spb', 'Санкт-Петербург'), 'спб': ('spb', 'Санкт-Петербург'),
    'казань': ('kazan', 'Казань'), 'казани': ('kazan', 'Казань'),
    'екатеринбург': ('ekaterinburg', 'Екатеринбург'), 'екатеринбурге': ('ekaterinburg', 'Екатеринбург'),
    'новосибирск': ('novosibirsk', 'Новосибирск'), 'новосибирске': ('novosibirsk', 'Новосибирск'),
    'самара': ('samara', 'Самара'), 'самаре': ('samara', 'Самара'),
    'челябинск': ('chelyabinsk', 'Челябинск'), 'челябинске': ('chelyabinsk', 'Челябинск'),
    'краснодар': ('krasnodar', 'Краснодар'), 'краснодаре': ('krasnodar', 'Краснодар'),
    'воронеж': ('voronezh', 'Воронеж'), 'воронеже': ('voronezh', 'Воронеж'),
    'пермь': ('perm', 'Пермь'), 'перми': ('perm', 'Пермь'),
    'уфа': ('ufa', 'Уфа'), 'уфе': ('ufa', 'Уфа'),
    'омск': ('omsk', 'Омск'), 'омске': ('omsk', 'Омск'),
}

_STOP = {
    'лучшие', 'лучший', 'лучших', 'топ', 'рейтинг', 'найди', 'найти', 'подбери', 'посоветуй',
    'порекомендуй', 'покажи', 'компания', 'компании', 'компаний', 'центр', 'центры', 'центров',
    'в', 'во', 'на', 'рядом', 'поблизости', 'около', 'москва', 'москве', 'москвы',
}

# Verified high-traffic Yell category slugs. Unknown categories are discovered
# from the city's public category page, so the provider is not limited to this list.
_DIRECT_CATEGORIES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'автосервис|авторемонт|сто\b|ремонт\w*\s+авто|машин\w*\s+ремонт', re.I), 'avtoservisy-i-tyuning'),
    (re.compile(r'стоматолог', re.I), 'stomatologii'),
)

_HINTS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r'слух|сурдолог|врач|доктор|клиник|медицин|диагност|анализ', re.I), ('медицин', 'клиник')),
    (re.compile(r'авто|машин|шиномонтаж|детейлинг|автомойк|техосмотр', re.I), ('автосервис', 'авто')),
    (re.compile(r'юрист|адвокат|нотариус|правов|банкротств', re.I), ('юридичес', 'юрист')),
    (re.compile(r'ресторан|кафе|бар\b|еда|пицц|суши', re.I), ('ресторан', 'кафе')),
    (re.compile(r'салон|красот|парикмах|барбершоп|маникюр|массаж', re.I), ('красот', 'салон')),
    (re.compile(r'фитнес|спортзал|тренаж|йога', re.I), ('фитнес', 'спорт')),
    (re.compile(r'ветеринар|ветклиник|животн', re.I), ('ветеринар',)),
    (re.compile(r'недвижим|риелтор|риэлтор', re.I), ('недвижим',)),
    (re.compile(r'строител|ремонт квартир|двер|отоплен', re.I), ('строител', 'ремонт')),
    (re.compile(r'курс|школ|обучен|образован|репетитор', re.I), ('образован', 'курс', 'школ')),
    (re.compile(r'аптек', re.I), ('аптек',)),
    (re.compile(r'химчист', re.I), ('химчист',)),
)

_CATEGORY_CACHE: dict[str, tuple[float, list[tuple[str, str]]]] = {}
_CATEGORY_CACHE_TTL = 30 * 60.0


def _clean(value: object, limit: int = 280) -> str:
    text = html.unescape(_TAG_RE.sub(' ', str(value or '')))
    return ' '.join(text.replace('\xa0', ' ').split())[:limit]


def _city(question: str) -> tuple[str, str]:
    text = normalized_question(question)
    for alias, pair in _CITY_SLUGS.items():
        if alias in text:
            return pair
    return 'moscow', 'Москва'


def _terms(question: str) -> tuple[str, ...]:
    result: list[str] = []
    for token in _WORD_RE.findall(normalized_question(question)):
        token = token.casefold()
        if len(token) < 4 or token in _STOP or token.isdigit():
            continue
        stem = token[:7] if len(token) >= 7 else token
        if stem not in result:
            result.append(stem)
    return tuple(result[:8])


def _hints(question: str) -> tuple[str, ...]:
    text = normalized_question(question)
    result: list[str] = []
    for pattern, hints in _HINTS:
        if pattern.search(text):
            result.extend(hint for hint in hints if hint not in result)
    return tuple(result)


def _canonical(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(('https', 'www.yell.ru', parsed.path, '', ''))


def _direct_category(question: str, city: str) -> str:
    text = normalized_question(question)
    for pattern, slug in _DIRECT_CATEGORIES:
        if pattern.search(text):
            return f'https://www.yell.ru/{city}/top/{slug}/'
    return ''


def _category_score(label: str, path: str, question: str) -> int:
    value = f'{_clean(label, 180)} {path}'.casefold()
    score = 0
    for term in _terms(question):
        if term in value:
            score += 2
    for hint in _hints(question):
        if hint in value:
            score += 4
    return score


def _category_links(root_url: str, body: str, city: str, question: str) -> list[str]:
    rows: list[tuple[int, str]] = []
    seen: set[str] = set()
    prefix = f'/{city}/top/'
    for href, label in _ANCHOR_RE.findall(body):
        url = urljoin(root_url, html.unescape(href))
        parsed = urlsplit(url)
        if (parsed.hostname or '').casefold().removeprefix('www.') != 'yell.ru':
            continue
        path = parsed.path
        if not path.startswith(prefix):
            continue
        parts = [part for part in path.split('/') if part]
        if len(parts) != 3:
            continue
        score = _category_score(label, path, question)
        if score <= 0:
            continue
        canonical = _canonical(url)
        if canonical in seen:
            continue
        seen.add(canonical)
        rows.append((score, canonical))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [url for _score, url in rows[:2]]


def _extract_address(plain: str, city_name: str) -> str:
    escaped = re.escape(city_name)
    patterns = (
        re.compile(rf'(?:Россия,\s*)?г\s+{escaped},?\s+(.{{3,150}}?)(?=\s+(?:метро|[0-5][.,]\d|8\s*\(|\+7|Закрыто|Открыто|$))', re.I),
        re.compile(rf'(?:Россия,\s*)?г\s+{escaped}[^|]{{3,170}}', re.I),
    )
    for pattern in patterns:
        match = pattern.search(plain)
        if match:
            value = match.group(0)
            return _clean(value, 220)
    return ''


def _listing_places(listing_url: str, body: str, city: str, city_name: str, limit: int) -> list[MapPlace]:
    if not body or _BLOCK_RE.search(body):
        return []
    matches = list(_ANCHOR_RE.finditer(body))
    rows: list[MapPlace] = []
    seen: set[str] = set()
    prefix = f'/{city}/com/'

    for index, match in enumerate(matches):
        href = html.unescape(match.group(1))
        url = urljoin(listing_url, href)
        parsed = urlsplit(url)
        if (parsed.hostname or '').casefold().removeprefix('www.') != 'yell.ru' or not parsed.path.startswith(prefix):
            continue
        canonical = _canonical(url)
        if canonical in seen:
            continue

        name = _clean(match.group(2), 150).strip(' -–—|:,.')
        if len(name) < 2 or name.casefold() in {'сеть', 'подробнее', 'читать дальше', 'показать полностью'}:
            continue

        # Listing pages already contain the useful evidence immediately around
        # each company link. Parsing it here avoids N extra card HTTP requests.
        next_start = matches[index + 1].start() if index + 1 < len(matches) else min(len(body), match.end() + 5000)
        window_end = min(max(next_start, match.end() + 500), match.end() + 5000)
        plain = _clean(body[match.start():window_end], 4500)

        rating = None
        reviews = None
        rr = _RATING_REVIEWS_RE.search(plain)
        if rr:
            try:
                rating = float(rr.group(1).replace(',', '.'))
                reviews = int(rr.group(2).replace(' ', ''))
            except ValueError:
                rating, reviews = None, None
        if rating is None:
            ro = _RATING_ONLY_RE.search(plain)
            if ro:
                try:
                    candidate = float(ro.group(1).replace(',', '.'))
                    rating = candidate if 0 < candidate <= 5.1 else None
                except ValueError:
                    rating = None

        phone = ''
        phone_match = _PHONE_RE.search(plain)
        if phone_match:
            digits = re.sub(r'\D', '', phone_match.group(0))
            if len(digits) == 11 and digits[0] in {'7', '8'}:
                if digits[0] == '8':
                    digits = '7' + digits[1:]
                phone = f'+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}'

        address = _extract_address(plain, city_name)
        seen.add(canonical)
        rows.append(MapPlace(
            provider='yell',
            name=name,
            card_url=canonical,
            address=address,
            phone=phone,
            rating=rating,
            reviews=reviews,
            source_url=listing_url,
        ))
        if len(rows) >= max(limit * 2, 12):
            break

    # Prefer evidence-backed entries with stronger review confidence while
    # retaining Yell's listing order as a stable tie-breaker.
    indexed = list(enumerate(rows))
    indexed.sort(
        key=lambda item: (
            item[1].rating is not None,
            item[1].rating or 0.0,
            item[1].reviews or 0,
            -item[0],
        ),
        reverse=True,
    )
    return [row for _index, row in indexed[:limit]]


async def _get(client: httpx.AsyncClient, url: str, *, max_bytes: int = 6_000_000) -> tuple[str, str]:
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        return url, ''
    body = response.text[:max_bytes]
    if _BLOCK_RE.search(body):
        return str(response.url), ''
    return str(response.url), body


async def _cached_categories(client: httpx.AsyncClient, city: str) -> list[tuple[str, str]]:
    now = time.monotonic()
    cached = _CATEGORY_CACHE.get(city)
    if cached and now - cached[0] <= _CATEGORY_CACHE_TTL:
        return list(cached[1])
    root_url = f'https://www.yell.ru/{city}/'
    final_url, body = await _get(client, root_url, max_bytes=4_000_000)
    if not body:
        return []
    rows: list[tuple[str, str]] = []
    prefix = f'/{city}/top/'
    seen: set[str] = set()
    for href, label in _ANCHOR_RE.findall(body):
        url = urljoin(final_url, html.unescape(href))
        parsed = urlsplit(url)
        if (parsed.hostname or '').casefold().removeprefix('www.') != 'yell.ru' or not parsed.path.startswith(prefix):
            continue
        parts = [part for part in parsed.path.split('/') if part]
        if len(parts) != 3:
            continue
        canonical = _canonical(url)
        if canonical in seen:
            continue
        seen.add(canonical)
        rows.append((_clean(label, 180), canonical))
    _CATEGORY_CACHE[city] = (now, list(rows))
    return rows


def _rank_cached_categories(rows: list[tuple[str, str]], question: str) -> list[str]:
    scored = [(_category_score(label, urlsplit(url).path, question), url) for label, url in rows]
    scored = [row for row in scored if row[0] > 0]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [url for _score, url in scored[:2]]


async def discover_yell(question: str, *, limit: int = 8) -> list[MapPlace]:
    """Fast keyless Yell discovery from public server-rendered listings.

    Common verticals use one HTTP request. Unknown verticals use at most two:
    city category index -> best matching category. No per-business fan-out.
    """
    city, city_name = _city(question)
    direct = _direct_category(question, city)
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.5',
        'Cache-Control': 'no-cache',
    }
    timeout = httpx.Timeout(3.2, connect=1.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False, headers=headers) as client:
        candidates: list[str] = [direct] if direct else []
        if not candidates:
            categories = await _cached_categories(client, city)
            candidates = _rank_cached_categories(categories, question)
        for url in candidates[:2]:
            final_url, body = await _get(client, url)
            rows = _listing_places(final_url, body, city, city_name, limit)
            if rows:
                return rows
    return []
