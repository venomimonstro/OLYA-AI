from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.services.discovery import DiscoveryError, SearchHit, canonical_result_url
from app.services.public_maps_discovery import MapPlace


_OID_FROM_URL_RE = re.compile(r"_(\d{6,24})(?:/|$|[?#])")
_PROFILE_RE = re.compile(r'https:\\/\\/yandex\.ru\\/profile\\/(\d{6,24})|https://yandex\.ru/profile/(\d{6,24})', re.I)
_OID_RE = re.compile(r'"oid"\s*:\s*"?(\d{6,24})"?', re.I)
_TITLE_RE = re.compile(r'<h1[^>]*>(.*?)</h1>', re.I | re.S)
_RATING_RE = re.compile(r'"rating"\s*:\s*\{[^{}]{0,220}?"value"\s*:\s*([0-5](?:[.,]\d+)?)', re.I | re.S)
_REVIEWS_RE = re.compile(r'"reviewsCount"\s*:\s*(\d+)', re.I)
_ADDRESS_RE = re.compile(r'"Address"\s*:\s*\{[^{}]{0,900}?"text"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', re.I | re.S)
_PHONE_RE = re.compile(r'(?:\+7|8)[\s()\-\d]{9,18}')
_SITE_URL_RE = re.compile(r'"url"\s*:\s*"(https?:\\?/\\?/[^"\\]+(?:\\.[^"\\]*)*)"[^{}]{0,220}?"text"\s*:\s*"(?:Сайт|Перейти на сайт)"', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCKED_RE = re.compile(r"captcha|smartcaptcha|проверка, что вы не робот|доступ временно ограничен", re.I)
_HEARING_RE = re.compile(r"слухопротез|слухов\w*\s+аппарат|сурдолог|центр\w*\s+слух", re.I)
_CITY_RE = re.compile(r"\b(москв\w*|санкт-петербург\w*|петербург\w*|спб|казан\w*|екатеринбург\w*|новосибирск\w*|самар\w*|челябинск\w*|красноярск\w*|тюмен\w*|уф\w*|перм\w*|сочи)\b", re.I)


@dataclass(frozen=True)
class YandexMedicineCard:
    place: MapPlace
    medicine_url: str


def _clean(value: str, limit: int = 240) -> str:
    value = html.unescape(_TAG_RE.sub(" ", str(value or "")))
    value = value.replace("\\u00a0", " ").replace("\\/", "/")
    return " ".join(value.split())[:limit]


def _decode_json_string(value: str) -> str:
    raw = str(value or "")
    try:
        return json.loads(f'"{raw}"')
    except (json.JSONDecodeError, TypeError):
        return raw.replace("\\/", "/").replace("\\u00a0", " ")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().removeprefix("www.")


def is_yandex_medicine_url(url: str) -> bool:
    parsed = urlsplit(str(url or ""))
    return _host(url) == "yandex.ru" and parsed.path.startswith("/medicine/clinic/")


def _oid(url: str, body: str) -> str:
    match = _OID_FROM_URL_RE.search(url)
    if match:
        return match.group(1)
    match = _PROFILE_RE.search(body)
    if match:
        return match.group(1) or match.group(2) or ""
    match = _OID_RE.search(body)
    return match.group(1) if match else ""


def _title(body: str) -> str:
    match = _TITLE_RE.search(body)
    if match:
        return _clean(match.group(1), 140)
    for pattern in (
        re.compile(r'"headerProps"\s*:\s*\{[^{}]{0,700}?"title"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', re.I | re.S),
        re.compile(r'<title>(.*?)</title>', re.I | re.S),
    ):
        match = pattern.search(body)
        if match:
            return _clean(_decode_json_string(match.group(1)), 140)
    return ""


def _rating(body: str) -> float | None:
    match = _RATING_RE.search(body)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    return round(value, 2) if 0 < value <= 5.1 else None


def _reviews(body: str) -> int | None:
    match = _REVIEWS_RE.search(body)
    return int(match.group(1)) if match else None


def _address(body: str) -> str:
    match = _ADDRESS_RE.search(body)
    if match:
        return _clean(_decode_json_string(match.group(1)), 220)
    match = re.search(r'(?:ул\.|улица|просп\.|проспект|ш\.|шоссе|пер\.|переулок|наб\.|набережная)[^<>\n]{3,120}', body, re.I)
    return _clean(match.group(0), 220) if match else ""


def _phone(body: str) -> str:
    match = _PHONE_RE.search(html.unescape(body))
    return _clean(match.group(0), 40) if match else ""


def _website(body: str) -> str:
    match = _SITE_URL_RE.search(body)
    if not match:
        return ""
    value = _decode_json_string(match.group(1)).replace("&amp;", "&")
    host = _host(value)
    if host and "yandex." not in host:
        return value[:500]
    return ""


def parse_yandex_medicine_page(url: str, body: str) -> YandexMedicineCard | None:
    if not body or _BLOCKED_RE.search(body):
        return None
    oid = _oid(url, body)
    name = _title(body)
    if not oid or not name:
        return None
    place = MapPlace(
        provider="yandex_maps",
        name=name,
        card_url=f"https://yandex.ru/profile/{oid}?lang=ru",
        address=_address(body),
        phone=_phone(body),
        website=_website(body),
        rating=_rating(body),
        reviews=_reviews(body),
        source_url=url,
    )
    return YandexMedicineCard(place=place, medicine_url=url)


async def _fetch_card(client: httpx.AsyncClient, url: str) -> YandexMedicineCard | None:
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return parse_yandex_medicine_page(str(response.url), response.text[:8_000_000])


def _query_variants(question: str) -> tuple[str, ...]:
    city_match = _CITY_RE.search(question)
    city = city_match.group(1) if city_match else "Москва"
    variants = [f"site:yandex.ru/medicine/clinic {question}"]
    if _HEARING_RE.search(question):
        variants.extend((
            f"site:yandex.ru/medicine/clinic слуховые аппараты {city}",
            f"site:yandex.ru/medicine/clinic сурдолог слуховые аппараты {city}",
        ))
    deduped: list[str] = []
    for value in variants:
        normalized = " ".join(value.split())
        if normalized not in deduped:
            deduped.append(normalized)
    return tuple(deduped)


async def discover_yandex_medicine(question: str, discovery, *, limit: int = 8) -> list[MapPlace]:
    """Discover Yandex organization cards through indexed Medicine pages.

    Queries are issued in parallel so hearing-center searches are not dependent
    on one exact wording. Indexed medicine pages are then fetched directly and
    parsed for the Yandex organization OID, rating, reviews, address and contacts.
    """
    queries = _query_variants(question)

    async def search_one(query: str) -> list[SearchHit]:
        try:
            return await asyncio.wait_for(
                discovery.search(query, count=max(10, limit), country="RU", language="ru"),
                timeout=4.0,
            )
        except (DiscoveryError, TimeoutError, asyncio.TimeoutError):
            return []

    batches = await asyncio.gather(*(search_one(query) for query in queries))

    urls: list[str] = []
    seen: set[str] = set()
    for batch in batches:
        for hit in batch:
            url = str(hit.url or "")
            if not is_yandex_medicine_url(url):
                continue
            key = canonical_result_url(url)
            if not key or key in seen:
                continue
            seen.add(key)
            urls.append(url)
            if len(urls) >= limit * 2:
                break
        if len(urls) >= limit * 2:
            break
    if not urls:
        return []

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    }
    timeout = httpx.Timeout(3.0, connect=1.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True, headers=headers) as client:
        cards = await asyncio.gather(*(_fetch_card(client, url) for url in urls[: limit * 2]))

    rows: list[MapPlace] = []
    seen_oids: set[str] = set()
    for card in cards:
        if card is None:
            continue
        oid_match = re.search(r"/profile/(\d+)", card.place.card_url)
        oid = oid_match.group(1) if oid_match else card.place.card_url
        if oid in seen_oids:
            continue
        seen_oids.add(oid)
        rows.append(card.place)
        if len(rows) >= limit:
            break
    return rows
