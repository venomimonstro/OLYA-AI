from __future__ import annotations

import asyncio
import html
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.services.discovery import canonical_result_url
from app.services.public_maps_discovery import MapPlace
from app.services.response_strategy import normalized_question


_ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
_H1_RE = re.compile(r'<h1[^>]*>(.*?)</h1>', re.I | re.S)
_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)
_TAG_RE = re.compile(r'<[^>]+>')
_WORD_RE = re.compile(r'[a-zа-яё0-9]+', re.I)
_BLOCK_RE = re.compile(r'captcha|smartcaptcha|access denied|доступ временно ограничен|проверка, что вы не робот', re.I)
_RATING_RE = re.compile(r'\b([0-5](?:[.,]\d+)?)\s+(\d[\d\s]{0,8})\s+отзыв', re.I)
_PHONE_RE = re.compile(r'(?:\+7|8)[\s()\-\d]{9,18}')
_ADDRESS_RE = re.compile(r'(?:Россия,\s*)?г\s+Москва[^<>\n]{3,180}', re.I)

_CITY_SLUGS = {
    'москва': 'moscow', 'москве': 'moscow', 'москвы': 'moscow',
    'санкт-петербург': 'spb', 'санкт-петербурге': 'spb', 'петербург': 'spb', 'спб': 'spb',
    'казань': 'kazan', 'казани': 'kazan',
    'екатеринбург': 'ekaterinburg', 'екатеринбурге': 'ekaterinburg',
    'новосибирск': 'novosibirsk', 'новосибирске': 'novosibirsk',
    'самара': 'samara', 'самаре': 'samara',
    'челябинск': 'chelyabinsk', 'челябинске': 'chelyabinsk',
    'краснодар': 'krasnodar', 'краснодаре': 'krasnodar',
    'воронеж': 'voronezh', 'воронеже': 'voronezh',
    'пермь': 'perm', 'перми': 'perm',
    'уфа': 'ufa', 'уфе': 'ufa',
    'омск': 'omsk', 'омске': 'omsk',
}

_STOP = {
    'лучшие', 'лучший', 'лучших', 'топ', 'рейтинг', 'найди', 'найти', 'подбери', 'посоветуй',
    'порекомендуй', 'покажи', 'компания', 'компании', 'компаний', 'центр', 'центры', 'центров',
    'в', 'во', 'на', 'рядом', 'поблизости', 'около', 'москва', 'москве', 'москвы',
}

_VERTICAL_HINTS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r'слух|сурдолог|врач|доктор|клиник|медицин|стоматолог|диагност|анализ', re.I), ('медицин', 'medicina')),
    (re.compile(r'авто|машин|шиномонтаж|детейлинг|автомойк|техосмотр', re.I), ('автосервис', 'avtoserv')),
    (re.compile(r'юрист|адвокат|нотариус|правов|банкротств', re.I), ('юридичес', 'jurid')),
    (re.compile(r'ресторан|кафе|бар\b|еда|пицц|суши', re.I), ('ресторан', 'restoran')),
    (re.compile(r'салон|красот|парикмах|барбершоп|маникюр|массаж', re.I), ('красот', 'salon')),
    (re.compile(r'фитнес|спортзал|тренаж|йога', re.I), ('фитнес', 'fitness')),
    (re.compile(r'ветеринар|ветклиник|животн', re.I), ('ветеринар', 'veterinar')),
    (re.compile(r'недвижим|риелтор|риэлтор', re.I), ('недвижим', 'nedvizhim')),
    (re.compile(r'строител|ремонт квартир|двер|отоплен', re.I), ('строител', 'stroitel')),
    (re.compile(r'курс|школ|обучен|образован|репетитор', re.I), ('образован', 'kurs')),
)


def _clean(value: object, limit: int = 280) -> str:
    text = html.unescape(_TAG_RE.sub(' ', str(value or '')))
    return ' '.join(text.replace('\xa0', ' ').split())[:limit]


def _city_slug(question: str) -> str:
    text = normalized_question(question)
    for alias, slug in _CITY_SLUGS.items():
        if alias in text:
            return slug
    return 'moscow'


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
    for pattern, hints in _VERTICAL_HINTS:
        if pattern.search(text):
            result.extend(hint for hint in hints if hint not in result)
    return tuple(result)


def _score(text: str, terms: tuple[str, ...], hints: tuple[str, ...] = ()) -> int:
    value = _clean(text, 700).casefold()
    return sum(2 for term in terms if term in value) + sum(3 for hint in hints if hint in value)


def _json_nodes(body: str):
    for match in _LD_RE.finditer(body):
        raw = html.unescape(match.group(1)).strip()
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, dict):
                continue
            graph = item.get('@graph')
            if isinstance(graph, list):
                for node in graph:
                    if isinstance(node, dict):
                        yield node
            yield item


def _address(value: object) -> str:
    if isinstance(value, str):
        return _clean(value, 220)
    if not isinstance(value, dict):
        return ''
    parts = [str(value.get(key) or '').strip() for key in ('streetAddress', 'addressLocality', 'addressRegion')]
    return _clean(', '.join(part for part in parts if part), 220)


def _rating(value: object) -> tuple[float | None, int | None]:
    if not isinstance(value, dict):
        return None, None
    try:
        rating = float(str(value.get('ratingValue') or '').replace(',', '.'))
        rating = round(rating, 2) if 0 < rating <= 5.1 else None
    except ValueError:
        rating = None
    reviews = None
    for key in ('reviewCount', 'ratingCount'):
        try:
            candidate = int(str(value.get(key) or '').replace(' ', ''))
        except ValueError:
            continue
        if candidate >= 0:
            reviews = candidate
            break
    return rating, reviews


def _canonical(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(('https', 'www.yell.ru', parsed.path, '', ''))


def _parse_card(url: str, body: str) -> MapPlace | None:
    if not body or _BLOCK_RE.search(body):
        return None
    for node in _json_nodes(body):
        node_type = str(node.get('@type') or '').casefold()
        if not any(token in node_type for token in ('localbusiness', 'organization', 'medical', 'restaurant', 'store', 'automotive')):
            continue
        name = _clean(node.get('name'), 150)
        if not name:
            continue
        rating, reviews = _rating(node.get('aggregateRating'))
        return MapPlace(
            provider='yell',
            name=name,
            card_url=_canonical(url),
            address=_address(node.get('address')),
            phone=_clean(node.get('telephone'), 40),
            website='',
            rating=rating,
            reviews=reviews,
            source_url=url,
        )

    h1 = _H1_RE.search(body)
    title = _TITLE_RE.search(body)
    name = _clean(h1.group(1), 150) if h1 else _clean(title.group(1), 180) if title else ''
    if not name:
        return None
    name = re.split(r'\s+[–—|-]\s+|\s+в\s+Москве', name, maxsplit=1)[0].strip()
    plain = _clean(body, 350_000)
    rating = None
    reviews = None
    match = _RATING_RE.search(plain)
    if match:
        try:
            rating = float(match.group(1).replace(',', '.'))
            reviews = int(match.group(2).replace(' ', ''))
        except ValueError:
            rating, reviews = None, None
    phone_match = _PHONE_RE.search(plain)
    address_match = _ADDRESS_RE.search(plain)
    return MapPlace(
        provider='yell',
        name=name,
        card_url=_canonical(url),
        address=_clean(address_match.group(0), 220) if address_match else '',
        phone=_clean(phone_match.group(0), 40) if phone_match else '',
        rating=rating,
        reviews=reviews,
        source_url=url,
    )


async def _get(client: httpx.AsyncClient, url: str, max_bytes: int = 5_000_000) -> tuple[str, str]:
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        return url, ''
    body = response.text[:max_bytes]
    if _BLOCK_RE.search(body):
        return str(response.url), ''
    return str(response.url), body


def _category_urls(root_url: str, body: str, city: str, question: str) -> list[str]:
    terms = _terms(question)
    hints = _hints(question)
    rows: list[tuple[int, str]] = []
    seen: set[str] = set()
    prefix = f'/{city}/top/'
    for href, label_html in _ANCHOR_RE.findall(body):
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
        score = _score(_clean(label_html, 180) + ' ' + path, terms, hints)
        if score <= 0:
            continue
        canonical = _canonical(url)
        if canonical in seen:
            continue
        seen.add(canonical)
        rows.append((score, canonical))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [url for _score_value, url in rows[:4]]


def _subcategory_urls(category_url: str, body: str, city: str, question: str) -> list[str]:
    terms = _terms(question)
    rows: list[tuple[int, str]] = []
    seen: set[str] = set()
    prefix = f'/{city}/top/'
    for href, label_html in _ANCHOR_RE.findall(body):
        url = urljoin(category_url, html.unescape(href))
        parsed = urlsplit(url)
        if (parsed.hostname or '').casefold().removeprefix('www.') != 'yell.ru' or not parsed.path.startswith(prefix):
            continue
        # Skip geo-filter pages; prefer pure service/category paths.
        if any(token in parsed.path for token in ('/metro_', '/rayon_', '/okrug_', '/street_', '/gorod_')):
            continue
        score = _score(_clean(label_html, 180) + ' ' + parsed.path, terms)
        if score <= 0:
            continue
        canonical = _canonical(url)
        if canonical in seen or canonical == _canonical(category_url):
            continue
        seen.add(canonical)
        rows.append((score, canonical))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [url for _score_value, url in rows[:3]]


def _card_urls(listing_url: str, body: str, city: str, limit: int) -> list[str]:
    prefix = f'/{city}/com/'
    result: list[str] = []
    seen: set[str] = set()
    for href, _label in _ANCHOR_RE.findall(body):
        url = urljoin(listing_url, html.unescape(href))
        parsed = urlsplit(url)
        if (parsed.hostname or '').casefold().removeprefix('www.') != 'yell.ru' or not parsed.path.startswith(prefix):
            continue
        canonical = _canonical(url)
        key = canonical_result_url(canonical)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(canonical)
        if len(result) >= limit:
            break
    return result


async def discover_yell(question: str, *, limit: int = 8) -> list[MapPlace]:
    city = _city_slug(question)
    root_url = f'https://www.yell.ru/{city}/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.5',
    }
    timeout = httpx.Timeout(3.6, connect=1.2)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False, headers=headers) as client:
        final_root, root_body = await _get(client, root_url, 4_000_000)
        if not root_body:
            return []
        categories = _category_urls(final_root, root_body, city, question)
        if not categories:
            return []
        category_pages = await asyncio.gather(*(_get(client, url) for url in categories))

        listing_pages: list[tuple[str, str]] = [row for row in category_pages if row[1]]
        sub_urls: list[str] = []
        seen_sub: set[str] = set()
        for category_url, body in listing_pages:
            for url in _subcategory_urls(category_url, body, city, question):
                if url not in seen_sub:
                    seen_sub.add(url)
                    sub_urls.append(url)
        if sub_urls:
            sub_pages = await asyncio.gather(*(_get(client, url) for url in sub_urls[:3]))
            listing_pages = [row for row in sub_pages if row[1]] + listing_pages

        urls: list[str] = []
        seen: set[str] = set()
        for listing_url, body in listing_pages:
            for url in _card_urls(listing_url, body, city, max(limit * 2, 12)):
                key = canonical_result_url(url)
                if not key or key in seen:
                    continue
                seen.add(key)
                urls.append(url)
                if len(urls) >= max(limit * 2, 12):
                    break
            if len(urls) >= max(limit * 2, 12):
                break
        if not urls:
            return []
        fetched = await asyncio.gather(*(_get(client, url, 4_000_000) for url in urls[: max(limit * 2, 12)]))

    result: list[MapPlace] = []
    seen_cards: set[str] = set()
    for final_url, body in fetched:
        card = _parse_card(final_url, body)
        if card is None:
            continue
        key = canonical_result_url(card.card_url)
        if not key or key in seen_cards:
            continue
        seen_cards.add(key)
        result.append(card)
        if len(result) >= limit:
            break
    return result
