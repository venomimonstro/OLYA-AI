from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.services.discovery import DiscoveryError, SearchHit, canonical_result_url
from app.services.public_maps_discovery import MapPlace


_MEDICINE_URL_RE = re.compile(r"https?://(?:www\.)?yandex\.ru/medicine/clinic/[^\s?#\"']+", re.I)
_OID_FROM_URL_RE = re.compile(r"_(\d{6,24})(?:/|$|[?#])")
_PROFILE_RE = re.compile(r'https:\\/\\/yandex\.ru\\/profile\\/(\d{6,24})|https://yandex\.ru/profile/(\d{6,24})', re.I)
_OID_RE = re.compile(r'"oid"\s*:\s*"?(\d{6,24})"?', re.I)
_TITLE_RE = re.compile(r'<h1[^>]*>(.*?)</h1>', re.I | re.S)
_RATING_RE = re.compile(r'"rating"\s*:\s*\{[^{}]{0,180}?"value"\s*:\s*([0-5](?:[.,]\d+)?)', re.I | re.S)
_REVIEWS_RE = re.compile(r'"reviewsCount"\s*:\s*(\d+)', re.I)
_ADDRESS_RE = re.compile(r'"Address"\s*:\s*\{[^{}]{0,600}?"text"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', re.I | re.S)
_PHONE_RE = re.compile(r'(?:\+7|8)[\s()\-\d]{9,18}')
_SITE_URL_RE = re.compile(r'"url"\s*:\s*"(https?:\\?/\\?/[^"\\]+(?:\\.[^"\\]*)*)"[^{}]{0,160}?"text"\s*:\s*"(?:Сайт|Перейти на сайт)"', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCKED_RE = re.compile(r"captcha|smartcaptcha|проверка, что вы не робот|доступ временно ограничен", re.I)


@dataclass(frozen=True)
class YandexMedicineCard:
    place: MapPlace
    medicine_url: str


def _clean(value: str, limit: int = 240) -> str:
    value = html.unescape(_TAG_RE.sub(" ", str(value or "")))
    value = value.replace("\\u00a0", " ").replace("\\/", "/")
    return " ".join(value.split())[:limit]


def _decode_json_string(value: str) -> str:
    try:
        return bytes(value, "utf-8").decode("unicode_escape").replace("\\/", "/")
    except UnicodeDecodeError:
        return value.replace("\\/", "/")


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
        re.compile(r'"headerProps"\s*:\s*\{[^{}]{0,600}?"title"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', re.I | re.S),
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
    # Server-rendered medicine pages also expose the visible Moscow address.
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
        # Yandex Medicine exposes the organization profile by OID. This is a
        # direct active Yandex organization card and is more stable than
        # inventing a /maps/org/<slug>/<id> URL when the slug is unknown.
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


async def discover_yandex_medicine(question: str, discovery, *, limit: int = 8) -> list[MapPlace]:
    """Discover Yandex organization cards through indexed Medicine pages.

    Direct Maps search is often JS-heavy or blocked on VPS IPs. Yandex Medicine
    pages are server-rendered and expose the same organization OID plus rating,
    reviews, address and contacts. Discovery remains keyless: ordinary search
    finds indexed medicine pages, then this parser reads only public HTML.
    """
    query = f"site:yandex.ru/medicine/clinic {question}"
    try:
        hits = await asyncio.wait_for(
            discovery.search(query, count=max(8, limit), country="RU", language="ru"),
            timeout=4.0,
        )
    except (DiscoveryError, TimeoutError, asyncio.TimeoutError):
        return []

    urls: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        url = str(hit.url or "")
        if not is_yandex_medicine_url(url):
            continue
        key = canonical_result_url(url)
        if not key or key in seen:
            continue
        seen.add(key)
        urls.append(url)
        if len(urls) >= limit:
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
        cards = await asyncio.gather(*(_fetch_card(client, url) for url in urls))

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
