from __future__ import annotations

import asyncio
import html
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import quote, quote_plus, urljoin, urlsplit

import httpx


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_JSON_RE = re.compile(
    r"<script[^>]+(?:type=[\"']application/json[\"']|id=[\"'][^\"']*(?:state|data)[^\"']*[\"'])[^>]*>(.*?)</script>",
    re.I | re.S,
)
_YANDEX_LINK_RE = re.compile(
    r"href=[\"'](?P<href>(?:https?://(?:www\.)?yandex\.(?:ru|com))?/maps/org/[^\"']+)[\"'][^>]*>(?P<body>.*?)</a>",
    re.I | re.S,
)
_TWOGIS_LINK_RE = re.compile(
    r"href=[\"'](?P<href>(?:https?://2gis\.ru)?/[a-z0-9_-]+/firm/(?P<id>\d+)[^\"']*)[\"'][^>]*>(?P<body>.*?)</a>",
    re.I | re.S,
)
_GOOGLE_LINK_RE = re.compile(
    r"href=[\"'](?P<href>(?:https?://www\.google\.[^/]+)?/maps/place/[^\"']+)[\"'][^>]*>(?P<body>.*?)</a>",
    re.I | re.S,
)
_PHONE_RE = re.compile(r"(?:\+7|8)[\s()\-\d]{9,18}")
_NUMERIC_ID_RE = re.compile(r"^\d{6,24}$")
_RANKING_WORDS_RE = re.compile(
    r"\b(?:лучши\w*|топ|рейтинг\w*|найди\w*|подбер\w*|посовет\w*|покажи\w*|"
    r"отзыв\w*|рекомендац\w*)\b",
    re.I,
)

_CITY_PROFILES = {
    "москва": ("moscow", "213", "Москва"),
    "москве": ("moscow", "213", "Москва"),
    "москвы": ("moscow", "213", "Москва"),
    "санкт-петербург": ("spb", "2", "Санкт-Петербург"),
    "санкт-петербурге": ("spb", "2", "Санкт-Петербург"),
    "петербург": ("spb", "2", "Санкт-Петербург"),
    "спб": ("spb", "2", "Санкт-Петербург"),
    "казань": ("kazan", "43", "Казань"),
    "казани": ("kazan", "43", "Казань"),
    "екатеринбург": ("ekaterinburg", "54", "Екатеринбург"),
    "екатеринбурге": ("ekaterinburg", "54", "Екатеринбург"),
    "новосибирск": ("novosibirsk", "65", "Новосибирск"),
    "новосибирске": ("novosibirsk", "65", "Новосибирск"),
    "самара": ("samara", "51", "Самара"),
    "самаре": ("samara", "51", "Самара"),
    "челябинск": ("chelyabinsk", "56", "Челябинск"),
    "челябинске": ("chelyabinsk", "56", "Челябинск"),
    "красноярск": ("krasnoyarsk", "62", "Красноярск"),
    "красноярске": ("krasnoyarsk", "62", "Красноярск"),
    "тюмень": ("tyumen", "55", "Тюмень"),
    "тюмени": ("tyumen", "55", "Тюмень"),
    "уфа": ("ufa", "172", "Уфа"),
    "уфе": ("ufa", "172", "Уфа"),
    "пермь": ("perm", "50", "Пермь"),
    "перми": ("perm", "50", "Пермь"),
    "сочи": ("sochi", "239", "Сочи"),
}


@dataclass(frozen=True)
class MapPlace:
    provider: str
    name: str
    card_url: str
    address: str = ""
    phone: str = ""
    website: str = ""
    rating: float | None = None
    reviews: int | None = None
    source_url: str = ""

    def public_source(self) -> dict:
        snippet_parts: list[str] = []
        if self.address:
            snippet_parts.append(self.address)
        if self.rating is not None:
            value = f"рейтинг {self.rating:g}"
            if self.reviews is not None:
                value += f", отзывов {self.reviews}"
            snippet_parts.append(value)
        if self.phone:
            snippet_parts.append(self.phone)
        return {
            "title": self.name,
            "url": self.card_url,
            "domain": (urlsplit(self.card_url).hostname or "").removeprefix("www."),
            "provider": self.provider,
            "snippet": " · ".join(snippet_parts)[:360],
        }


_CACHE: dict[str, tuple[float, list[MapPlace]]] = {}
_CACHE_TTL = 10 * 60.0


def _clean_text(value: object, *, limit: int = 240) -> str:
    text = html.unescape(_TAG_RE.sub(" ", str(value or "")))
    text = " ".join(text.replace("\\u00a0", " ").replace("\xa0", " ").split())
    return text[:limit]


def _city_profile(question: str) -> tuple[str, str, str]:
    text = str(question or "").casefold()
    for alias, profile in _CITY_PROFILES.items():
        if alias in text:
            return profile
    return "moscow", "213", "Москва"


def _normalized_map_query(question: str, city_name: str) -> str:
    text = " ".join(str(question or "").split())
    text = _RANKING_WORDS_RE.sub(" ", text)
    text = re.sub(r"\b(?:в|во)\s+(?:москве|москвы|санкт-петербурге|петербурге|казани|екатеринбурге|новосибирске|самаре|челябинске|красноярске|тюмени|уфе|перми|сочи)\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ,.-")
    if city_name.casefold() not in text.casefold():
        text = f"{text} {city_name}".strip()
    return text[:220]


def _balanced_json_after(text: str, marker: str) -> str | None:
    start = text.find(marker)
    if start < 0:
        return None
    open_at = text.find("{", start + len(marker))
    if open_at < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(open_at, min(len(text), open_at + 8_000_000)):
        ch = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_at : index + 1]
    return None


def _json_payloads(body: str) -> list[object]:
    payloads: list[object] = []
    seen: set[str] = set()
    for marker in (
        "window.__INITIAL_STATE__",
        "window.__PRELOADED_STATE__",
        "window.__SSR_STATE__",
        "__INITIAL_STATE__",
        "__NEXT_DATA__",
    ):
        raw = _balanced_json_after(body, marker)
        if raw and raw not in seen:
            seen.add(raw)
            try:
                payloads.append(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                pass
    for match in _SCRIPT_JSON_RE.finditer(body):
        raw = html.unescape(match.group(1)).strip()
        if not raw or raw in seen or len(raw) > 8_000_000:
            continue
        seen.add(raw)
        try:
            payloads.append(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            continue
    return payloads


def _walk_json(value: object):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json(item)


def _float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if 0 < result <= 5.1:
        return round(result, 2)
    return None


def _int(value: object) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _first_str(node: dict, *keys: str) -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_text(value)
    return ""


def _phone_from(value: object) -> str:
    if isinstance(value, str):
        match = _PHONE_RE.search(value)
        return _clean_text(match.group(0), limit=40) if match else ""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                found = _first_str(item, "formatted", "number", "value", "phone")
                if found:
                    return found[:40]
            else:
                found = _phone_from(item)
                if found:
                    return found
    if isinstance(value, dict):
        return _first_str(value, "formatted", "number", "value", "phone")[:40]
    return ""


def _website_from(node: dict) -> str:
    for key in ("website", "site", "url", "displayUrl"):
        value = node.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            host = (urlsplit(value).hostname or "").casefold()
            if not any(token in host for token in ("yandex.", "2gis.", "google.")):
                return value[:500]
    for key in ("urls", "links", "contactGroups", "contact_groups"):
        value = node.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found = _website_from(item)
                    if found:
                        return found
    return ""


def _yandex_from_json(payloads: list[object], source_url: str) -> list[MapPlace]:
    rows: list[MapPlace] = []
    seen: set[str] = set()
    for payload in payloads:
        for raw_node in _walk_json(payload):
            node = raw_node
            properties = node.get("properties")
            if isinstance(properties, dict) and isinstance(properties.get("CompanyMetaData"), dict):
                node = {**node, **properties["CompanyMetaData"]}
            business_id = str(node.get("businessId") or node.get("business_id") or node.get("id") or "").strip()
            name = _first_str(node, "name", "title", "shortTitle", "short_title")
            address = _first_str(node, "address", "fullAddress", "full_address", "description")
            if not (_NUMERIC_ID_RE.fullmatch(business_id) and name and (address or node.get("categories") or node.get("rating"))):
                continue
            if business_id in seen:
                continue
            seen.add(business_id)
            rating = _float(node.get("rating") or node.get("ratingValue") or node.get("score"))
            reviews = _int(node.get("reviewsCount") or node.get("reviewCount") or node.get("reviews_count") or node.get("ratingCount"))
            rows.append(MapPlace(
                provider="yandex_maps",
                name=name,
                card_url=f"https://yandex.ru/maps/org/{business_id}",
                address=address,
                phone=_phone_from(node.get("phones") or node.get("phone")),
                website=_website_from(node),
                rating=rating,
                reviews=reviews,
                source_url=source_url,
            ))
            if len(rows) >= 12:
                return rows
    return rows


def _yandex_from_links(body: str, source_url: str) -> list[MapPlace]:
    rows: list[MapPlace] = []
    seen: set[str] = set()
    for match in _YANDEX_LINK_RE.finditer(body):
        href = html.unescape(match.group("href"))
        url = urljoin("https://yandex.com", href)
        id_match = re.search(r"/maps/org/(?:[^/]+/)?(\d{6,24})(?:/|$|\?)", url)
        if not id_match or id_match.group(1) in seen:
            continue
        name = _clean_text(match.group("body"), limit=120)
        if len(name) < 2:
            continue
        seen.add(id_match.group(1))
        rows.append(MapPlace("yandex_maps", name, f"https://yandex.ru/maps/org/{id_match.group(1)}", source_url=source_url))
        if len(rows) >= 12:
            break
    return rows


def _twogis_from_json(payloads: list[object], city_slug: str, source_url: str) -> list[MapPlace]:
    rows: list[MapPlace] = []
    seen: set[str] = set()
    for payload in payloads:
        for node in _walk_json(payload):
            branch_id = str(node.get("id") or node.get("branch_id") or "").strip()
            name_ex = node.get("name_ex") if isinstance(node.get("name_ex"), dict) else {}
            name = _first_str(node, "name", "full_name", "title") or _first_str(name_ex, "primary")
            address = _first_str(node, "full_address_name", "address_name", "address")
            type_value = str(node.get("type") or "").casefold()
            if not (_NUMERIC_ID_RE.fullmatch(branch_id) and name and type_value in {"branch", "firm", "organization", ""} and (address or node.get("rubrics") or node.get("reviews"))):
                continue
            if branch_id in seen:
                continue
            seen.add(branch_id)
            reviews_obj = node.get("reviews") if isinstance(node.get("reviews"), dict) else {}
            rows.append(MapPlace(
                provider="2gis",
                name=name,
                card_url=f"https://2gis.ru/{city_slug}/firm/{branch_id}",
                address=address,
                phone=_phone_from(node.get("contact_groups") or node.get("phones") or node.get("phone")),
                website=_website_from(node),
                rating=_float(reviews_obj.get("rating") or node.get("rating")),
                reviews=_int(reviews_obj.get("review_count") or reviews_obj.get("general_review_count") or node.get("reviews_count")),
                source_url=source_url,
            ))
            if len(rows) >= 12:
                return rows
    return rows


def _twogis_from_links(body: str, city_slug: str, source_url: str) -> list[MapPlace]:
    rows: list[MapPlace] = []
    seen: set[str] = set()
    for match in _TWOGIS_LINK_RE.finditer(body):
        branch_id = match.group("id")
        if branch_id in seen:
            continue
        name = _clean_text(match.group("body"), limit=120)
        if len(name) < 2:
            continue
        seen.add(branch_id)
        rows.append(MapPlace("2gis", name, f"https://2gis.ru/{city_slug}/firm/{branch_id}", source_url=source_url))
        if len(rows) >= 12:
            break
    return rows


def _google_from_links(body: str, source_url: str) -> list[MapPlace]:
    rows: list[MapPlace] = []
    seen: set[str] = set()
    for match in _GOOGLE_LINK_RE.finditer(body):
        url = html.unescape(urljoin("https://www.google.com", match.group("href")))
        if url in seen:
            continue
        name = _clean_text(match.group("body"), limit=120)
        if len(name) < 2:
            continue
        seen.add(url)
        rows.append(MapPlace("google_maps", name, url, source_url=source_url))
        if len(rows) >= 10:
            break
    return rows


async def _get(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    content_type = str(response.headers.get("content-type") or "").casefold()
    if content_type and "html" not in content_type and "text" not in content_type:
        return ""
    return response.text[:8_000_000]


class PublicMapsDiscovery:
    """Low-rate direct discovery from public map search pages, without paid APIs.

    It never bypasses CAPTCHA or authentication. A blocked provider simply returns
    no rows; the remaining providers and the ordinary web fallback can continue.
    """

    def __init__(self, *, timeout_seconds: float = 4.5) -> None:
        self.timeout_seconds = max(1.5, min(float(timeout_seconds), 7.0))

    async def search(self, question: str) -> list[MapPlace]:
        key = " ".join(str(question or "").casefold().split())[:300]
        cached = _CACHE.get(key)
        now = time.monotonic()
        if cached and now - cached[0] <= _CACHE_TTL:
            return list(cached[1])

        city_slug, region_id, city_name = _city_profile(question)
        query = _normalized_map_query(question, city_name)
        encoded_path = quote(query, safe="")
        encoded_qs = quote_plus(query)
        urls = (
            ("yandex", f"https://yandex.com/maps/{region_id}/{city_slug}/search/{encoded_path}/"),
            ("yandex", f"https://yandex.com/maps/?text={encoded_qs}"),
            ("2gis", f"https://2gis.ru/{city_slug}/search/{encoded_path}"),
            ("google", f"https://www.google.com/maps/search/{encoded_path}?hl=ru"),
        )

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.6",
            "Cache-Control": "no-cache",
        }
        timeout = httpx.Timeout(self.timeout_seconds, connect=min(1.5, self.timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True, headers=headers) as client:
            async def fetch(provider: str, url: str) -> tuple[str, str, str]:
                try:
                    body = await _get(client, url)
                    low = body.casefold()
                    if any(marker in low for marker in ("captcha", "smartcaptcha", "unusual traffic", "проверка, что вы не робот")):
                        return provider, url, ""
                    return provider, url, body
                except (httpx.HTTPError, TimeoutError, ValueError):
                    return provider, url, ""

            responses = await asyncio.gather(*(fetch(provider, url) for provider, url in urls))

        rows: list[MapPlace] = []
        for provider, url, body in responses:
            if not body:
                continue
            payloads = _json_payloads(body)
            if provider == "yandex":
                found = _yandex_from_json(payloads, url) or _yandex_from_links(body, url)
            elif provider == "2gis":
                found = _twogis_from_json(payloads, city_slug, url) or _twogis_from_links(body, city_slug, url)
            else:
                found = _google_from_links(body, url)
            rows.extend(found)

        deduped: list[MapPlace] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            marker = (row.provider, row.card_url)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(row)
            if len(deduped) >= 24:
                break
        _CACHE[key] = (now, list(deduped))
        return deduped


def map_search_links(name: str, *, city_slug: str = "moscow", address: str = "") -> dict[str, str]:
    query = " ".join(part for part in (name, address) if part).strip()
    encoded = quote_plus(query)
    path_encoded = quote(query, safe="")
    return {
        "yandex": f"https://yandex.ru/maps/?text={encoded}",
        "google": f"https://www.google.com/maps/search/?api=1&query={encoded}",
        "2gis": f"https://2gis.ru/{city_slug}/search/{path_encoded}",
    }
