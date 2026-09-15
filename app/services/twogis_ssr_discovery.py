from __future__ import annotations

import asyncio
import html
import json
import re
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app.services.public_maps_discovery import MapPlace, _city_profile, _normalized_map_query


_TAG_RE = re.compile(r"<[^>]+>")
_ANCHOR_FIRM_RE = re.compile(
    r"<a\b[^>]*href=[\"'](?P<href>[^\"']*/firm/(?P<id>\d{6,24})[^\"']*)[\"'][^>]*>(?P<body>.*?)</a>",
    re.I | re.S,
)
_FIRM_RE = re.compile(r"/(?P<city>[a-z0-9_-]+)/firm/(?P<id>\d{6,24})(?:[/?#]|$)", re.I)
_JSON_NAME_RE = re.compile(
    r'\"(?:name|full_name|title|primary)\"\s*:\s*\"(?P<value>(?:\\.|[^\"\\]){2,180})\"',
    re.I,
)
_JSON_ADDRESS_RE = re.compile(
    r'\"(?:full_address_name|address_name|address)\"\s*:\s*\"(?P<value>(?:\\.|[^\"\\]){3,240})\"',
    re.I,
)
_JSON_RATING_RE = re.compile(r'\"rating\"\s*:\s*(?:\"?)(?P<value>[0-5](?:[.,]\d+)?)(?:\"?)', re.I)
_JSON_REVIEWS_RE = re.compile(
    r'\"(?:review_count|general_review_count|reviews_count|rating_count)\"\s*:\s*(?P<value>\d+)',
    re.I,
)
_PLAIN_RATING_RE = re.compile(r"\b(?P<rating>[0-5](?:[.,]\d+)?)\s+(?P<reviews>\d[\d\s]{0,7})\s+(?:оценк\w*|отзыв\w*)", re.I)
_ADDRESS_CITY_RE = re.compile(
    r"(?P<value>[А-ЯA-ZЁ0-9][^<>\n]{3,170}?(?:улиц\w*|ул\.|проспект\w*|просп\.|шоссе|проезд\w*|переул\w*|наб\.|набережн\w*)[^<>\n]{0,100}?,\s*(?:г\.?\s*)?[А-ЯЁ][А-Яа-яЁё-]{2,})",
    re.I,
)
_BLOCK_RE = re.compile(r"captcha|smartcaptcha|access denied|доступ временно ограничен|проверка, что вы не робот", re.I)
_GENERIC_NAMES = {
    "2гис", "карта", "маршрут", "контакты", "инфо", "отзывы", "цены", "фото",
    "автосервис", "легковой автосервис", "москва", "филиалы", "филиал",
}


def _clean(value: object, limit: int = 240) -> str:
    text = html.unescape(_TAG_RE.sub(" ", str(value or "")))
    text = text.replace("\\u00a0", " ").replace("\xa0", " ").replace("\\/", "/")
    return " ".join(text.split())[:limit]


def _decode_json_string(value: str) -> str:
    raw = str(value or "")
    try:
        return json.loads(f'"{raw}"')
    except (json.JSONDecodeError, TypeError):
        return raw.replace("\\/", "/").replace("\\u00a0", " ")


def _normalize_html(body: str) -> str:
    # 2GIS uses several SSR encodings. This copy is only used for discovery;
    # the original response is never executed or interpreted as JavaScript.
    return (
        html.unescape(str(body or ""))
        .replace("\\u002F", "/")
        .replace("\\u002f", "/")
        .replace("\\/", "/")
        .replace('\\"', '"')
    )


def _valid_name(value: str) -> bool:
    text = _clean(value, 160).strip(" -–—|:,.\t\n")
    if len(text) < 2 or len(text) > 140:
        return False
    low = text.casefold()
    if low in _GENERIC_NAMES:
        return False
    if low.startswith(("http://", "https://")):
        return False
    if re.fullmatch(r"[\d\W_]+", text):
        return False
    return True


def _nearby_value(pattern: re.Pattern[str], text: str, center: int, *, before: int = 2200, after: int = 2600) -> str:
    start = max(0, center - before)
    end = min(len(text), center + after)
    window = text[start:end]
    best: tuple[int, str] | None = None
    for match in pattern.finditer(window):
        value = _clean(_decode_json_string(match.group("value")), 240)
        if not value:
            continue
        absolute = start + match.start()
        distance = abs(absolute - center)
        if best is None or distance < best[0]:
            best = (distance, value)
    return best[1] if best else ""


def _nearby_number(pattern: re.Pattern[str], text: str, center: int) -> str:
    start = max(0, center - 1600)
    end = min(len(text), center + 2200)
    window = text[start:end]
    best: tuple[int, str] | None = None
    for match in pattern.finditer(window):
        absolute = start + match.start()
        distance = abs(absolute - center)
        value = match.group("value")
        if best is None or distance < best[0]:
            best = (distance, value)
    return best[1] if best else ""


def _rating(value: str) -> float | None:
    try:
        result = float(str(value or "").replace(",", "."))
    except ValueError:
        return None
    return round(result, 2) if 0 < result <= 5.1 else None


def _reviews(value: str) -> int | None:
    try:
        result = int(re.sub(r"\D", "", str(value or "")))
    except ValueError:
        return None
    return result if 0 <= result <= 10_000_000 else None


def _plain_context(body: str, start: int, end: int) -> str:
    return _clean(body[max(0, start - 300): min(len(body), end + 1500)], 2200)


def _from_anchor_matches(body: str, city_slug: str, source_url: str) -> list[MapPlace]:
    rows: list[MapPlace] = []
    seen: set[str] = set()
    for match in _ANCHOR_FIRM_RE.finditer(body):
        branch_id = match.group("id")
        if branch_id in seen:
            continue
        href = urljoin("https://2gis.ru", html.unescape(match.group("href")))
        parsed = urlsplit(href)
        path = parsed.path.casefold()
        if f"/{city_slug}/firm/{branch_id}" not in path:
            continue
        name = _clean(match.group("body"), 150).strip(" -–—|:,.\t\n")
        if not _valid_name(name):
            continue
        context = _plain_context(body, match.start(), match.end())
        rating = None
        reviews = None
        score = _PLAIN_RATING_RE.search(context)
        if score:
            rating = _rating(score.group("rating"))
            reviews = _reviews(score.group("reviews"))
        address = ""
        address_match = _ADDRESS_CITY_RE.search(context)
        if address_match:
            address = _clean(address_match.group("value"), 220)
        seen.add(branch_id)
        rows.append(MapPlace(
            provider="2gis",
            name=name,
            card_url=f"https://2gis.ru/{city_slug}/firm/{branch_id}",
            address=address,
            rating=rating,
            reviews=reviews,
            source_url=source_url,
        ))
        if len(rows) >= 16:
            break
    return rows


def _from_embedded_state(body: str, city_slug: str, source_url: str) -> list[MapPlace]:
    normalized = _normalize_html(body)
    rows: list[MapPlace] = []
    seen: set[str] = set()
    for match in _FIRM_RE.finditer(normalized):
        if match.group("city").casefold() != city_slug.casefold():
            continue
        branch_id = match.group("id")
        if branch_id in seen:
            continue
        center = match.start()
        name = _nearby_value(_JSON_NAME_RE, normalized, center)
        if not _valid_name(name):
            # Some SSR blocks contain the human-readable name outside JSON.
            plain = _plain_context(normalized, match.start(), match.end())
            candidates = [part.strip() for part in re.split(r"[|·\n]", plain) if _valid_name(part.strip())]
            name = min(candidates, key=len) if candidates else ""
        if not _valid_name(name):
            continue
        address = _nearby_value(_JSON_ADDRESS_RE, normalized, center)
        rating = _rating(_nearby_number(_JSON_RATING_RE, normalized, center))
        reviews = _reviews(_nearby_number(_JSON_REVIEWS_RE, normalized, center))
        seen.add(branch_id)
        rows.append(MapPlace(
            provider="2gis",
            name=_clean(name, 140),
            card_url=f"https://2gis.ru/{city_slug}/firm/{branch_id}",
            address=_clean(address, 220),
            rating=rating,
            reviews=reviews,
            source_url=source_url,
        ))
        if len(rows) >= 16:
            break
    return rows


async def discover_twogis_ssr(question: str, *, timeout_seconds: float = 4.0, limit: int = 12) -> list[MapPlace]:
    city_slug, _region_id, city_name = _city_profile(question)
    query = _normalized_map_query(question, city_name)
    # City is already encoded in the URL namespace. Removing the trailing city
    # produces a cleaner 2GIS query such as "автосервисы" instead of
    # "автосервисы Москва" and improves server-rendered result quality.
    query = re.sub(rf"\s+{re.escape(city_name)}$", "", query, flags=re.I).strip() or query
    url = f"https://2gis.ru/{city_slug}/search/{quote(query, safe='')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        "Cache-Control": "no-cache",
    }
    timeout = httpx.Timeout(max(1.5, min(float(timeout_seconds), 6.0)), connect=1.2)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
    except (httpx.HTTPError, TimeoutError, ValueError):
        return []
    body = response.text[:8_000_000]
    if not body or _BLOCK_RE.search(body):
        return []

    final_url = str(response.url)
    rows = _from_anchor_matches(body, city_slug, final_url)
    if len(rows) < min(5, limit):
        embedded = _from_embedded_state(body, city_slug, final_url)
        seen = {row.card_url for row in rows}
        rows.extend(row for row in embedded if row.card_url not in seen)

    # Preserve the ranking order emitted by 2GIS and keep the request bounded.
    return rows[: max(1, limit)]
