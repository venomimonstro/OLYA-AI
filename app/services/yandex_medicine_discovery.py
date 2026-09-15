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
_HTML_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)
_RATING_RE = re.compile(r'"rating"\s*:\s*\{[^{}]{0,220}?"value"\s*:\s*([0-5](?:[.,]\d+)?)', re.I | re.S)
_REVIEWS_RE = re.compile(r'"reviewsCount"\s*:\s*(\d+)', re.I)
_ADDRESS_RE = re.compile(r'"Address"\s*:\s*\{[^{}]{0,900}?"text"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', re.I | re.S)
_ADDRESS_LOCALITY_RE = re.compile(r'"addressLocality"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', re.I)
_LOCALITY_RE = re.compile(r'"(?:localityName|cityName)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', re.I)
_TEL_HREF_RE = re.compile(r'href=["\']tel:([^"\']+)["\']', re.I)
_PHONE_JSON_RE = re.compile(r'"(?:telephone|phone|formatted|number)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', re.I)
_SITE_URL_RE = re.compile(r'"url"\s*:\s*"(https?:\\?/\\?/[^"\\]+(?:\\.[^"\\]*)*)"[^{}]{0,220}?"text"\s*:\s*"(?:Сайт|Перейти на сайт)"', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCKED_RE = re.compile(r"captcha|smartcaptcha|проверка, что вы не робот|доступ временно ограничен", re.I)
_HEARING_RE = re.compile(r"слухопротез|слухов\w*\s+аппарат|сурдолог|центр\w*\s+слух", re.I)
_CITY_RE = re.compile(r"\b(москв\w*|санкт-петербург\w*|петербург\w*|спб|казан\w*|екатеринбург\w*|новосибирск\w*|самар\w*|челябинск\w*|красноярск\w*|тюмен\w*|уф\w*|перм\w*|сочи|волгоград\w*|благовещенск\w*|пенз\w*|майкоп\w*|иркутск\w*|ульяновск\w*|подольск\w*)\b", re.I)
_OWNER_CONFIRMED_RE = re.compile(r"\s*Информация об организации подтверждена владельцем\.?\s*", re.I)

_CITY_CANONICAL: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bмоскв\w*\b", re.I), "москва"),
    (re.compile(r"\b(?:санкт-петербург\w*|петербург\w*|спб)\b", re.I), "санкт-петербург"),
    (re.compile(r"\bказан\w*\b", re.I), "казань"),
    (re.compile(r"\bекатеринбург\w*\b", re.I), "екатеринбург"),
    (re.compile(r"\bновосибирск\w*\b", re.I), "новосибирск"),
    (re.compile(r"\bсамар\w*\b", re.I), "самара"),
    (re.compile(r"\bчелябинск\w*\b", re.I), "челябинск"),
    (re.compile(r"\bкрасноярск\w*\b", re.I), "красноярск"),
    (re.compile(r"\bтюмен\w*\b", re.I), "тюмень"),
    (re.compile(r"\bуф\w*\b", re.I), "уфа"),
    (re.compile(r"\bперм\w*\b", re.I), "пермь"),
    (re.compile(r"\bсочи\b", re.I), "сочи"),
    (re.compile(r"\bволгоград\w*\b", re.I), "волгоград"),
    (re.compile(r"\bблаговещенск\w*\b", re.I), "благовещенск"),
    (re.compile(r"\bпенз\w*\b", re.I), "пенза"),
    (re.compile(r"\bмайкоп\w*\b", re.I), "майкоп"),
    (re.compile(r"\bиркутск\w*\b", re.I), "иркутск"),
    (re.compile(r"\bульяновск\w*\b", re.I), "ульяновск"),
    (re.compile(r"\bподольск\w*\b", re.I), "подольск"),
)


@dataclass(frozen=True)
class YandexMedicineCard:
    place: MapPlace
    medicine_url: str
    locality: str = ""
    page_title: str = ""


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


def _canonical_city(value: str) -> str:
    text = _clean(value, 300).casefold()
    for pattern, canonical in _CITY_CANONICAL:
        if pattern.search(text):
            return canonical
    return ""


def _requested_city(question: str) -> str:
    return _canonical_city(question) or "москва"


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
        value = _clean(match.group(1), 180)
        return _OWNER_CONFIRMED_RE.sub(" ", value).strip()[:140]
    for pattern in (
        re.compile(r'"headerProps"\s*:\s*\{[^{}]{0,700}?"title"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', re.I | re.S),
        _HTML_TITLE_RE,
    ):
        match = pattern.search(body)
        if match:
            value = _clean(_decode_json_string(match.group(1)), 180)
            return _OWNER_CONFIRMED_RE.sub(" ", value).strip()[:140]
    return ""


def _page_title(body: str) -> str:
    match = _HTML_TITLE_RE.search(body)
    return _clean(match.group(1), 320) if match else ""


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


def _locality(body: str) -> str:
    for pattern in (_ADDRESS_LOCALITY_RE, _LOCALITY_RE):
        match = pattern.search(body)
        if match:
            return _clean(_decode_json_string(match.group(1)), 100)
    return ""


def _normalize_phone(value: str) -> str:
    raw = _decode_json_string(html.unescape(str(value or "")))
    if "x" in raw.casefold() or "*" in raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 11 or digits[0] not in {"7", "8"}:
        return ""
    if digits[0] == "8":
        digits = "7" + digits[1:]
    return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"


def _phone(body: str) -> str:
    for pattern in (_TEL_HREF_RE, _PHONE_JSON_RE):
        for match in pattern.finditer(body):
            phone = _normalize_phone(match.group(1))
            if phone:
                return phone
    return ""


def _website(body: str) -> str:
    match = _SITE_URL_RE.search(body)
    if not match:
        return ""
    value = _decode_json_string(match.group(1)).replace("&amp;", "&")
    host = _host(value)
    if host and "yandex." not in host:
        return value[:500]
    return ""


def _matches_requested_city(question: str, card: YandexMedicineCard) -> bool:
    target = _requested_city(question)
    locality_city = _canonical_city(card.locality)
    if locality_city:
        return locality_city == target

    # Yandex Medicine titles usually include the city even when the visible
    # street address omits it (e.g. Moscow metro-based addresses).
    title_city = _canonical_city(card.page_title)
    if title_city:
        return title_city == target

    address_city = _canonical_city(card.place.address)
    if address_city:
        return address_city == target

    # Indexed Medicine is a global corpus. Without positive locality evidence
    # we fail closed instead of leaking a business from another Russian city.
    return False


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
    return YandexMedicineCard(
        place=place,
        medicine_url=url,
        locality=_locality(body),
        page_title=_page_title(body),
    )


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


def _hit_matches_city(hit: SearchHit, question: str) -> bool:
    target = _requested_city(question)
    haystack = " ".join((str(hit.title or ""), str(hit.snippet or ""))).strip()
    city = _canonical_city(haystack)
    return not city or city == target


async def discover_yandex_medicine(question: str, discovery, *, limit: int = 8) -> list[MapPlace]:
    """Discover indexed Yandex Medicine cards with strict city validation."""
    queries = _query_variants(question)

    async def search_one(query: str) -> list[SearchHit]:
        try:
            return await asyncio.wait_for(
                discovery.search(query, count=max(12, limit * 2), country="RU", language="ru"),
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
            if not is_yandex_medicine_url(url) or not _hit_matches_city(hit, question):
                continue
            key = canonical_result_url(url)
            if not key or key in seen:
                continue
            seen.add(key)
            urls.append(url)
            if len(urls) >= limit * 3:
                break
        if len(urls) >= limit * 3:
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
        cards = await asyncio.gather(*(_fetch_card(client, url) for url in urls[: limit * 3]))

    rows: list[MapPlace] = []
    seen_oids: set[str] = set()
    for card in cards:
        if card is None or not _matches_requested_city(question, card):
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
