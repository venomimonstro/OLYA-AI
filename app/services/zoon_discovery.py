from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.services.discovery import DiscoveryError, canonical_result_url
from app.services.public_maps_discovery import MapPlace
from app.services.response_strategy import normalized_question


_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)
_H1_RE = re.compile(r'<h1[^>]*>(.*?)</h1>', re.I | re.S)
_TAG_RE = re.compile(r'<[^>]+>')
_BLOCK_RE = re.compile(r'captcha|smartcaptcha|access denied|доступ временно ограничен|проверка, что вы не робот', re.I)
_RATING_TEXT_RE = re.compile(
    r'(?:средняя\s+оценка\s*[-—:]?\s*|рейтинг\s*[-—:]?\s*)([0-5](?:[.,]\d+)?)',
    re.I,
)
_REVIEW_TEXT_RE = re.compile(r'(\d[\d\s]{0,8})\s+отзыв', re.I)
_ADDRESS_TITLE_RE = re.compile(r'по\s+адресу\s+(.{5,180}?)\s+на\s+Zoon', re.I)

_CITY_SLUGS = {
    'москва': 'msk', 'москве': 'msk', 'москвы': 'msk',
    'санкт-петербург': 'spb', 'санкт-петербурге': 'spb', 'петербург': 'spb', 'спб': 'spb',
    'казань': 'kazan', 'казани': 'kazan',
    'екатеринбург': 'ekaterinburg', 'екатеринбурге': 'ekaterinburg',
    'новосибирск': 'novosibirsk', 'новосибирске': 'novosibirsk',
    'самара': 'samara', 'самаре': 'samara',
    'челябинск': 'chelyabinsk', 'челябинске': 'chelyabinsk',
    'красноярск': 'krasnoyarsk', 'красноярске': 'krasnoyarsk',
    'тюмень': 'tyumen', 'тюмени': 'tyumen',
    'уфа': 'ufa', 'уфе': 'ufa',
    'пермь': 'perm', 'перми': 'perm',
    'сочи': 'sochi',
    'калининград': 'kaliningrad', 'калининграде': 'kaliningrad',
    'воронеж': 'voronezh', 'воронеже': 'voronezh',
    'краснодар': 'krasnodar', 'краснодаре': 'krasnodar',
    'омск': 'omsk', 'омске': 'omsk',
    'ростов-на-дону': 'rostov', 'ростове-на-дону': 'rostov',
    'нижний новгород': 'nnovgorod', 'нижнем новгороде': 'nnovgorod',
}


@dataclass(frozen=True)
class ZoonCard:
    place: MapPlace
    source_url: str


def _clean(value: object, limit: int = 260) -> str:
    text = html.unescape(_TAG_RE.sub(' ', str(value or '')))
    return ' '.join(text.replace('\xa0', ' ').split())[:limit]


def _city_slug(question: str) -> str:
    text = normalized_question(question)
    for alias, slug in _CITY_SLUGS.items():
        if alias in text:
            return slug
    return 'msk'


def _is_zoon_url(url: str, city_slug: str) -> bool:
    parsed = urlsplit(str(url or ''))
    host = (parsed.hostname or '').casefold().removeprefix('www.')
    path = parsed.path.casefold()
    if host != 'zoon.ru':
        return False
    if not path.startswith(f'/{city_slug}/'):
        return False
    if any(token in path for token in ('/article/', '/type/', '/network/', '/chains/', '/specialists/')):
        return False
    return True


def _canonical_card_url(url: str) -> str:
    parsed = urlsplit(str(url or ''))
    path = parsed.path
    for suffix in ('/reviews/', '/reviews', '/prices/', '/prices'):
        if path.endswith(suffix):
            path = path[: -len(suffix)] + '/'
            break
    return urlunsplit(('https', 'zoon.ru', path, '', ''))


def _json_objects(body: str):
    for match in _LD_RE.finditer(body):
        raw = html.unescape(match.group(1)).strip()
        if not raw or len(raw) > 2_000_000:
            continue
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
        elif isinstance(value, dict):
            graph = value.get('@graph')
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict):
                        yield item
            yield value


def _address_from_ld(value: object) -> str:
    if isinstance(value, str):
        return _clean(value, 220)
    if not isinstance(value, dict):
        return ''
    parts = []
    for key in ('streetAddress', 'addressLocality', 'addressRegion'):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            parts.append(item.strip())
    return _clean(', '.join(parts), 220)


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
            current = int(str(value.get(key) or '').replace(' ', ''))
        except ValueError:
            continue
        if current >= 0:
            reviews = current
            break
    return rating, reviews


def _structured_card(url: str, body: str) -> ZoonCard | None:
    for node in _json_objects(body):
        node_type = node.get('@type')
        if isinstance(node_type, list):
            types = {str(item).casefold() for item in node_type}
        else:
            types = {str(node_type or '').casefold()}
        if not types.intersection({'localbusiness', 'organization', 'medicalorganization', 'dentist', 'restaurant', 'store', 'automotiverepair'}):
            continue
        name = _clean(node.get('name'), 140)
        if not name:
            continue
        rating, reviews = _rating(node.get('aggregateRating'))
        phone = _clean(node.get('telephone'), 40)
        website = ''
        same_as = node.get('sameAs')
        if isinstance(same_as, str) and same_as.startswith(('http://', 'https://')) and 'zoon.ru' not in same_as:
            website = same_as[:500]
        place = MapPlace(
            provider='zoon',
            name=name,
            card_url=_canonical_card_url(url),
            address=_address_from_ld(node.get('address')),
            phone=phone,
            website=website,
            rating=rating,
            reviews=reviews,
            source_url=url,
        )
        return ZoonCard(place=place, source_url=url)
    return None


def _fallback_card(url: str, body: str) -> ZoonCard | None:
    h1 = _H1_RE.search(body)
    title_match = _TITLE_RE.search(body)
    title = _clean(h1.group(1), 140) if h1 else _clean(title_match.group(1), 220) if title_match else ''
    if not title:
        return None
    name = title
    for sep in (' по адресу ', ' — ', ' | ', ' на Zoon'):
        if sep in name:
            name = name.split(sep, 1)[0].strip()
    name = re.sub(r'^(?:Отзывы\s+(?:о|об)|Цены\s+(?:в|на)|Отзывы\s+)', '', name, flags=re.I).strip()
    if len(name) < 2:
        return None

    rating = None
    match = _RATING_TEXT_RE.search(_clean(body, 300_000))
    if match:
        try:
            candidate = float(match.group(1).replace(',', '.'))
            if 0 < candidate <= 5.1:
                rating = round(candidate, 2)
        except ValueError:
            pass
    reviews = None
    review_match = _REVIEW_TEXT_RE.search(_clean(body, 300_000))
    if review_match:
        try:
            reviews = int(review_match.group(1).replace(' ', ''))
        except ValueError:
            pass
    address = ''
    if title_match:
        am = _ADDRESS_TITLE_RE.search(_clean(title_match.group(1), 400))
        if am:
            address = _clean(am.group(1), 220)

    return ZoonCard(
        place=MapPlace(
            provider='zoon',
            name=name,
            card_url=_canonical_card_url(url),
            address=address,
            rating=rating,
            reviews=reviews,
            source_url=url,
        ),
        source_url=url,
    )


def parse_zoon_page(url: str, body: str) -> ZoonCard | None:
    if not body or _BLOCK_RE.search(body):
        return None
    return _structured_card(url, body) or _fallback_card(url, body)


async def _fetch_one(client: httpx.AsyncClient, url: str) -> ZoonCard | None:
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return parse_zoon_page(str(response.url), response.text[:6_000_000])


async def discover_zoon(question: str, discovery, *, limit: int = 8) -> list[MapPlace]:
    """Generic Russian business discovery through public Zoon pages.

    This is category-agnostic: medicine, auto, beauty, legal, education,
    restaurants, shops and other local businesses use the same pipeline.
    Search discovery finds indexed Zoon organization pages; public HTML/JSON-LD
    supplies name, address, rating and review count without a paid API.
    """
    city_slug = _city_slug(question)
    queries = (
        f'site:zoon.ru/{city_slug}/ {question}',
        f'site:zoon.ru/{city_slug}/reviews/ {question}',
    )

    batches = []
    for query in queries:
        try:
            rows = await asyncio.wait_for(
                discovery.search(query, count=max(10, limit * 2), country='RU', language='ru'),
                timeout=3.8,
            )
        except (DiscoveryError, TimeoutError, asyncio.TimeoutError):
            rows = []
        batches.append(rows)

    urls: list[str] = []
    seen: set[str] = set()
    for batch in batches:
        for hit in batch:
            url = str(hit.url or '')
            if not _is_zoon_url(url, city_slug):
                continue
            canonical = canonical_result_url(_canonical_card_url(url))
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            urls.append(url)
            if len(urls) >= limit:
                break
        if len(urls) >= limit:
            break
    if not urls:
        return []

    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.5',
    }
    timeout = httpx.Timeout(3.2, connect=1.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True, headers=headers) as client:
        cards = await asyncio.gather(*(_fetch_one(client, url) for url in urls))

    result: list[MapPlace] = []
    seen_cards: set[str] = set()
    for card in cards:
        if card is None:
            continue
        key = canonical_result_url(card.place.card_url)
        if not key or key in seen_cards:
            continue
        seen_cards.add(key)
        result.append(card.place)
        if len(result) >= limit:
            break
    return result
