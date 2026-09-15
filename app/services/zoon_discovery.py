from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.services.discovery import canonical_result_url
from app.services.public_maps_discovery import MapPlace
from app.services.response_strategy import normalized_question


_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)
_H1_RE = re.compile(r'<h1[^>]*>(.*?)</h1>', re.I | re.S)
_ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r'<[^>]+>')
_BLOCK_RE = re.compile(r'captcha|smartcaptcha|access denied|доступ временно ограничен|проверка, что вы не робот', re.I)
_RATING_TEXT_RE = re.compile(
    r'(?:средняя\s+оценка\s*[-—:]?\s*|рейтинг\s*[-—:]?\s*)([0-5](?:[.,]\d+)?)',
    re.I,
)
_REVIEW_TEXT_RE = re.compile(r'(\d[\d\s]{0,8})\s+(?:отзыв|оценк)', re.I)
_ADDRESS_TITLE_RE = re.compile(r'по\s+адресу\s+(.{5,180}?)\s+на\s+Zoon', re.I)
_WORD_RE = re.compile(r'[a-zа-яё0-9]+', re.I)

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

_STOP = {
    'лучшие', 'лучший', 'лучших', 'топ', 'рейтинг', 'рейтингу', 'найди', 'найти', 'подбери',
    'посоветуй', 'порекомендуй', 'покажи', 'компания', 'компании', 'компаний', 'центр', 'центры',
    'центров', 'в', 'во', 'на', 'рядом', 'поблизости', 'около', 'москва', 'москве', 'москвы',
}

# Broad semantic hints are only used to enter Zoon's live taxonomy. Fine-grained
# service/type selection is then discovered from the category page itself.
_VERTICAL_HINTS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r'слух|сурдолог|врач|доктор|клиник|медицин|стоматолог|диагност|анализ', re.I), ('medical', 'медицин')),
    (re.compile(r'авто|машин|шиномонтаж|детейлинг|автомойк|техосмотр', re.I), ('autoservice', 'автосервис')),
    (re.compile(r'юрист|адвокат|нотариус|правов|банкротств', re.I), ('law', 'юридичес')),
    (re.compile(r'ресторан|кафе|бар\b|еда|пицц|суши', re.I), ('restaurants', 'ресторан')),
    (re.compile(r'салон|красот|парикмах|барбершоп|маникюр|массаж', re.I), ('beauty', 'красот')),
    (re.compile(r'фитнес|спортзал|тренаж|йога', re.I), ('fitness', 'фитнес')),
    (re.compile(r'ветеринар|ветклиник|животн', re.I), ('vet', 'ветеринар')),
    (re.compile(r'недвижим|риелтор|риэлтор', re.I), ('realty', 'недвижим')),
    (re.compile(r'строител|ремонт квартир|двер|отоплен', re.I), ('building', 'строител')),
    (re.compile(r'курс|школ|обучен|образован|репетитор', re.I), ('trainings', 'образован')),
    (re.compile(r'магазин|товар|супермаркет|рынок', re.I), ('shops', 'магазин')),
    (re.compile(r'туризм|отел|гостиниц|хостел|туроператор', re.I), ('tourism', 'туризм')),
)


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


def _query_terms(question: str) -> tuple[str, ...]:
    result: list[str] = []
    for token in _WORD_RE.findall(normalized_question(question)):
        token = token.casefold()
        if len(token) < 4 or token in _STOP or token.isdigit():
            continue
        stem = token[:7] if len(token) >= 7 else token
        if stem not in result:
            result.append(stem)
    return tuple(result[:8])


def _semantic_hints(question: str) -> tuple[str, ...]:
    text = normalized_question(question)
    values: list[str] = []
    for pattern, hints in _VERTICAL_HINTS:
        if pattern.search(text):
            for hint in hints:
                if hint not in values:
                    values.append(hint)
    return tuple(values)


def _text_score(text: str, terms: tuple[str, ...], hints: tuple[str, ...] = ()) -> int:
    haystack = _clean(text, 700).casefold()
    score = sum(2 for term in terms if term in haystack)
    score += sum(3 for hint in hints if hint in haystack)
    return score


def _is_zoon_url(url: str, city_slug: str) -> bool:
    parsed = urlsplit(str(url or ''))
    host = (parsed.hostname or '').casefold().removeprefix('www.')
    path = parsed.path.casefold()
    if host != 'zoon.ru':
        return False
    allowed_prefixes = ('/', f'/{city_slug}/') if city_slug == 'msk' else (f'/{city_slug}/',)
    if not any(path.startswith(prefix) for prefix in allowed_prefixes):
        return False
    if any(token in path for token in ('/article/', '/network/', '/chains/', '/specialists/')):
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
        if not types.intersection({
            'localbusiness', 'organization', 'medicalorganization', 'dentist', 'restaurant', 'store',
            'automotiverepair', 'legalservice', 'beautysalon', 'healthclub', 'educationalorganization',
        }):
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

    plain = _clean(body, 350_000)
    rating = None
    match = _RATING_TEXT_RE.search(plain)
    if match:
        try:
            candidate = float(match.group(1).replace(',', '.'))
            if 0 < candidate <= 5.1:
                rating = round(candidate, 2)
        except ValueError:
            pass
    reviews = None
    review_match = _REVIEW_TEXT_RE.search(plain)
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


async def _fetch_text(client: httpx.AsyncClient, url: str, *, max_bytes: int = 6_000_000) -> tuple[str, str]:
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        return url, ''
    body = response.text[:max_bytes]
    if _BLOCK_RE.search(body):
        return str(response.url), ''
    return str(response.url), body


def _category_candidates(root_url: str, body: str, city_slug: str, question: str) -> list[str]:
    terms = _query_terms(question)
    hints = _semantic_hints(question)
    rows: list[tuple[int, str]] = []
    seen: set[str] = set()
    root_path = urlsplit(root_url).path.rstrip('/')
    for href, label_html in _ANCHOR_RE.findall(body):
        url = urljoin(root_url, html.unescape(href))
        parsed = urlsplit(url)
        host = (parsed.hostname or '').casefold().removeprefix('www.')
        if host != 'zoon.ru':
            continue
        path = parsed.path.rstrip('/')
        if not path or path == root_path:
            continue
        if '/type/' in path or '/specialists' in path or '/article/' in path or '/reviews' in path:
            continue
        segments = [part for part in path.split('/') if part]
        # Moscow's landing is zoon.ru/, but its category URLs are /msk/<category>/.
        valid_depths = {1, 2} if city_slug == 'msk' else {2}
        if len(segments) not in valid_depths:
            continue
        if city_slug == 'msk' and len(segments) == 2 and segments[0] != 'msk':
            continue
        if city_slug != 'msk' and segments[0] != city_slug:
            continue
        label = _clean(label_html, 180)
        score = _text_score(label + ' ' + path.replace('/', ' '), terms, hints)
        if score <= 0:
            continue
        canonical = urlunsplit(('https', 'zoon.ru', parsed.path if parsed.path.endswith('/') else parsed.path + '/', '', ''))
        if canonical in seen:
            continue
        seen.add(canonical)
        rows.append((score, canonical))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [url for _score, url in rows[:3]]


def _subcategory_candidates(category_url: str, body: str, question: str) -> list[str]:
    terms = _query_terms(question)
    rows: list[tuple[int, str]] = []
    seen: set[str] = set()
    for href, label_html in _ANCHOR_RE.findall(body):
        url = urljoin(category_url, html.unescape(href))
        parsed = urlsplit(url)
        if (parsed.hostname or '').casefold().removeprefix('www.') != 'zoon.ru':
            continue
        if '/type/' not in parsed.path:
            continue
        label = _clean(label_html, 220)
        score = _text_score(label + ' ' + parsed.path.replace('/', ' '), terms)
        if score <= 0:
            continue
        canonical = urlunsplit(('https', 'zoon.ru', parsed.path if parsed.path.endswith('/') else parsed.path + '/', '', ''))
        if canonical in seen:
            continue
        seen.add(canonical)
        rows.append((score, canonical))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [url for _score, url in rows[:2]]


def _organization_links(category_url: str, body: str, city_slug: str, question: str, limit: int) -> list[str]:
    terms = _query_terms(question)
    category_segments = [part for part in urlsplit(category_url).path.split('/') if part]
    candidates: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    position = 0
    for href, label_html in _ANCHOR_RE.findall(body):
        position += 1
        url = urljoin(category_url, html.unescape(href))
        if not _is_zoon_url(url, city_slug):
            continue
        parsed = urlsplit(url)
        path = parsed.path
        if any(token in path for token in ('/type/', '/specialists/', '/article/', '/network/', '/chains/')):
            continue
        segments = [part for part in path.split('/') if part]
        if len(segments) <= len(category_segments):
            continue
        label = _clean(label_html, 220)
        if len(label) < 3:
            continue
        canonical = _canonical_card_url(url)
        key = canonical_result_url(canonical)
        if not key or key in seen:
            continue
        seen.add(key)
        score = _text_score(label, terms)
        candidates.append((score, -position, canonical))
    candidates.sort(reverse=True)
    return [url for _score, _position, url in candidates[:limit]]


async def discover_zoon(question: str, discovery=None, *, limit: int = 8) -> list[MapPlace]:
    """Generic direct Zoon discovery without depending on SERP.

    Pipeline: city landing -> live taxonomy category -> optional matching type ->
    organization cards -> JSON-LD/HTML extraction. The taxonomy is read from
    Zoon itself, while broad hints only bridge user vocabulary to a top-level
    vertical such as hearing aids -> medical.
    """
    _ = discovery
    city_slug = _city_slug(question)
    root_url = 'https://zoon.ru/' if city_slug == 'msk' else f'https://zoon.ru/{city_slug}/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.5',
        'Cache-Control': 'no-cache',
    }
    timeout = httpx.Timeout(3.5, connect=1.2)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True, headers=headers) as client:
        final_root, root_body = await _fetch_text(client, root_url, max_bytes=4_000_000)
        if not root_body:
            return []
        categories = _category_candidates(final_root, root_body, city_slug, question)
        if not categories:
            return []

        category_pages = await asyncio.gather(*(_fetch_text(client, url, max_bytes=5_000_000) for url in categories))
        listing_pages: list[tuple[str, str]] = []
        subcategory_urls: list[str] = []
        seen_sub: set[str] = set()
        for category_url, body in category_pages:
            if not body:
                continue
            listing_pages.append((category_url, body))
            for sub_url in _subcategory_candidates(category_url, body, question):
                if sub_url not in seen_sub:
                    seen_sub.add(sub_url)
                    subcategory_urls.append(sub_url)

        if subcategory_urls:
            sub_pages = await asyncio.gather(*(_fetch_text(client, url, max_bytes=5_000_000) for url in subcategory_urls[:3]))
            # Matching type pages are more specific, so process them first.
            listing_pages = [row for row in sub_pages if row[1]] + listing_pages

        card_urls: list[str] = []
        seen: set[str] = set()
        for listing_url, body in listing_pages:
            for url in _organization_links(listing_url, body, city_slug, question, max(limit * 2, 14)):
                key = canonical_result_url(url)
                if not key or key in seen:
                    continue
                seen.add(key)
                card_urls.append(url)
                if len(card_urls) >= max(limit * 2, 14):
                    break
            if len(card_urls) >= max(limit * 2, 14):
                break
        if not card_urls:
            return []

        fetched = await asyncio.gather(*(_fetch_text(client, url) for url in card_urls[: max(limit * 2, 14)]))

    result: list[MapPlace] = []
    seen_cards: set[str] = set()
    for final_url, body in fetched:
        if not body:
            continue
        card = parse_zoon_page(final_url, body)
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
