from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlsplit

import httpx

from app.services.public_maps_discovery import MapPlace, _city_profile
from app.services.response_strategy import normalized_question


_ENDPOINTS = (
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
_CACHE: dict[str, tuple[float, list[MapPlace]]] = {}
_CACHE_TTL_SECONDS = 15 * 60.0

_WORD = re.compile(r"[a-zа-яё0-9]+", re.I)
_STOP = {
    "лучший", "лучшие", "лучших", "топ", "рейтинг", "найди", "найти", "подбери", "посоветуй",
    "порекомендуй", "покажи", "выбери", "выбрать", "хороший", "хорошие", "рядом", "отзывы", "отзыв",
    "москва", "москве", "москвы", "санкт", "петербург", "петербурге", "спб", "казань", "казани",
    "екатеринбург", "екатеринбурге", "новосибирск", "новосибирске", "самара", "самаре", "челябинск",
    "челябинске", "красноярск", "красноярске", "тюмень", "тюмени", "уфа", "уфе", "пермь", "перми",
    "сочи", "воронеж", "воронеже", "краснодар", "краснодаре", "омск", "омске", "нижний", "новгород",
    "в", "во", "на", "около", "поблизости",
}


@dataclass(frozen=True)
class _Category:
    pattern: re.Pattern[str]
    selectors: tuple[tuple[str, str | None], ...]


_CATEGORIES: tuple[_Category, ...] = (
    _Category(re.compile(r"автосервис|авторемонт|ремонт\w*\s+(?:авто|машин)|сто\b|сервис\w*\s+авто", re.I), (
        ("shop", "car_repair"), ("amenity", "car_repair"), ("service:vehicle:car_repair", None),
    )),
    _Category(re.compile(r"шиномонтаж|шины|покрышк", re.I), (("shop", "tyres"),)),
    _Category(re.compile(r"автомойк", re.I), (("amenity", "car_wash"),)),
    _Category(re.compile(r"детейлинг", re.I), (("shop", "car_repair"), ("service:vehicle:detailing", None))),
    _Category(re.compile(r"слухопротез|слухов\w*\s+аппарат|сурдолог|аудиолог", re.I), (
        ("shop", "hearing_aids"), ("healthcare", "audiologist"),
    )),
    _Category(re.compile(r"стоматолог", re.I), (("amenity", "dentist"), ("healthcare", "dentist"))),
    _Category(re.compile(r"клиник|медицин\w*\s+центр|диагност\w*\s+центр", re.I), (
        ("amenity", "clinic"), ("healthcare", "clinic"),
    )),
    _Category(re.compile(r"аптек", re.I), (("amenity", "pharmacy"),)),
    _Category(re.compile(r"ветеринар|ветклиник", re.I), (("amenity", "veterinary"),)),
    _Category(re.compile(r"юрист|адвокат|юридичес", re.I), (("office", "lawyer"),)),
    _Category(re.compile(r"нотариус", re.I), (("office", "notary"),)),
    _Category(re.compile(r"риелтор|риэлтор|недвижим", re.I), (("office", "estate_agent"),)),
    _Category(re.compile(r"страхов", re.I), (("office", "insurance"),)),
    _Category(re.compile(r"банк\w*", re.I), (("amenity", "bank"),)),
    _Category(re.compile(r"ресторан", re.I), (("amenity", "restaurant"),)),
    _Category(re.compile(r"кафе|кофейн", re.I), (("amenity", "cafe"),)),
    _Category(re.compile(r"бар\b", re.I), (("amenity", "bar"), ("amenity", "pub"))),
    _Category(re.compile(r"пицц", re.I), (("cuisine", "pizza"),)),
    _Category(re.compile(r"суши", re.I), (("cuisine", "sushi"),)),
    _Category(re.compile(r"фитнес|спортзал|тренаж", re.I), (("leisure", "fitness_centre"),)),
    _Category(re.compile(r"йога", re.I), (("leisure", "fitness_centre"), ("sport", "yoga"))),
    _Category(re.compile(r"салон\w*\s+красот|косметолог|маникюр|педикюр", re.I), (("shop", "beauty"),)),
    _Category(re.compile(r"парикмах|барбершоп", re.I), (("shop", "hairdresser"),)),
    _Category(re.compile(r"массаж", re.I), (("shop", "massage"), ("healthcare", "physiotherapist"))),
    _Category(re.compile(r"химчист", re.I), (("shop", "dry_cleaning"),)),
    _Category(re.compile(r"прачеч", re.I), (("shop", "laundry"),)),
    _Category(re.compile(r"ателье|портн", re.I), (("craft", "tailor"),)),
    _Category(re.compile(r"типограф|печать|копицентр", re.I), (("shop", "copyshop"), ("craft", "printer"))),
    _Category(re.compile(r"цветоч|цветы", re.I), (("shop", "florist"),)),
    _Category(re.compile(r"мебел", re.I), (("shop", "furniture"),)),
    _Category(re.compile(r"пекар", re.I), (("shop", "bakery"),)),
    _Category(re.compile(r"отел|гостиниц", re.I), (("tourism", "hotel"), ("tourism", "hostel"))),
    _Category(re.compile(r"языков\w*\s+школ|английск\w*\s+школ", re.I), (("amenity", "language_school"),)),
    _Category(re.compile(r"автошкол", re.I), (("amenity", "driving_school"),)),
    _Category(re.compile(r"школ\w*", re.I), (("amenity", "school"),)),
    _Category(re.compile(r"строител\w*\s+компани|строител\w*\s+фирм", re.I), (("office", "construction_company"), ("craft", "builder"))),
    _Category(re.compile(r"электрик", re.I), (("craft", "electrician"),)),
    _Category(re.compile(r"сантехник", re.I), (("craft", "plumber"),)),
    _Category(re.compile(r"ремонт\w*\s+(?:телефон|смартфон)", re.I), (("shop", "mobile_phone"),)),
    _Category(re.compile(r"ремонт\w*\s+(?:компьютер|ноутбук)", re.I), (("shop", "computer"),)),
)

_BUSINESS_KEYS = {"shop", "amenity", "office", "craft", "tourism", "leisure", "healthcare"}


def _category(question: str) -> tuple[tuple[str, str | None], ...]:
    text = normalized_question(question)
    for item in _CATEGORIES:
        if item.pattern.search(text):
            return item.selectors
    return ()


def _fallback_keyword(question: str) -> str:
    tokens: list[str] = []
    for token in _WORD.findall(normalized_question(question)):
        low = token.casefold()
        if len(low) < 4 or low in _STOP or low.isdigit():
            continue
        tokens.append(token)
    return " ".join(tokens[:2])[:80]


def _escape_overpass(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _selector_line(key: str, value: str | None) -> str:
    k = _escape_overpass(key)
    if value is None:
        return f'nwr["{k}"](area.searchArea);'
    return f'nwr["{k}"="{_escape_overpass(value)}"](area.searchArea);'


def build_overpass_query(question: str) -> str:
    _slug, _region_id, city_name = _city_profile(question)
    selectors = _category(question)
    lines: list[str]
    if selectors:
        lines = [_selector_line(key, value) for key, value in selectors]
    else:
        keyword = _fallback_keyword(question)
        if not keyword:
            return ""
        pattern = _escape_overpass(re.escape(keyword))
        lines = [
            f'nwr["name"~"{pattern}",i](area.searchArea);',
            f'nwr["brand"~"{pattern}",i](area.searchArea);',
            f'nwr["operator"~"{pattern}",i](area.searchArea);',
        ]
    return (
        '[out:json][timeout:10];\n'
        f'area["boundary"="administrative"]["name"="{_escape_overpass(city_name)}"]->.searchArea;\n'
        '(\n  ' + '\n  '.join(lines) + '\n);\n'
        'out center tags 60;'
    )


def _address(tags: dict[str, object]) -> str:
    full = str(tags.get("addr:full") or "").strip()
    if full:
        return full[:220]
    street = str(tags.get("addr:street") or tags.get("addr:place") or "").strip()
    number = str(tags.get("addr:housenumber") or "").strip()
    city = str(tags.get("addr:city") or "").strip()
    parts = []
    if street:
        parts.append((street + (f", {number}" if number else "")).strip())
    elif number:
        parts.append(number)
    if city:
        parts.append(city)
    return ", ".join(parts)[:220]


def _phone(tags: dict[str, object]) -> str:
    value = str(tags.get("contact:phone") or tags.get("phone") or "").strip()
    return value[:80]


def _website(tags: dict[str, object]) -> str:
    value = str(tags.get("contact:website") or tags.get("website") or "").strip()
    if not value:
        return ""
    if value.startswith("www."):
        value = "https://" + value
    parsed = urlsplit(value)
    return value[:500] if parsed.scheme in {"http", "https"} and parsed.hostname else ""


def _name(tags: dict[str, object]) -> str:
    for key in ("name", "brand", "operator"):
        value = " ".join(str(tags.get(key) or "").split()).strip()
        if len(value) >= 2:
            return value[:140]
    return ""


def _is_business(tags: dict[str, object], *, fixed_category: bool) -> bool:
    if fixed_category:
        return True
    return any(str(tags.get(key) or "").strip() for key in _BUSINESS_KEYS)


def _score(tags: dict[str, object]) -> int:
    score = 0
    if _address(tags): score += 3
    if _phone(tags): score += 3
    if _website(tags): score += 3
    if tags.get("opening_hours"): score += 2
    if tags.get("brand"): score += 1
    if tags.get("wikidata") or tags.get("wikipedia"): score += 1
    return score


def parse_overpass_payload(payload: object, question: str, *, limit: int = 12) -> list[MapPlace]:
    if not isinstance(payload, dict):
        return []
    elements = payload.get("elements")
    if not isinstance(elements, list):
        return []
    fixed = bool(_category(question))
    ranked: list[tuple[int, int, MapPlace]] = []
    seen: set[str] = set()
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            continue
        tags = element.get("tags")
        if not isinstance(tags, dict) or not _is_business(tags, fixed_category=fixed):
            continue
        name = _name(tags)
        if not name:
            continue
        osm_type = str(element.get("type") or "")
        osm_id = element.get("id")
        if osm_type not in {"node", "way", "relation"} or not isinstance(osm_id, int):
            continue
        card = f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
        if card in seen:
            continue
        seen.add(card)
        place = MapPlace(
            provider="osm",
            name=name,
            card_url=card,
            address=_address(tags),
            phone=_phone(tags),
            website=_website(tags),
            source_url=card,
        )
        ranked.append((_score(tags), -index, place))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [place for _score_value, _order, place in ranked[: max(1, limit)]]


async def _one(endpoint: str, query: str, timeout_seconds: float) -> list[MapPlace]:
    headers = {
        "User-Agent": "OLYA-AI/1.0 (OpenStreetMap business lookup; contact via project administrator)",
        "Accept": "application/json",
    }
    timeout = httpx.Timeout(timeout_seconds, connect=min(2.0, timeout_seconds))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False, headers=headers) as client:
            response = await client.post(endpoint, data={"data": query})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return []
    return parse_overpass_payload(payload, _CURRENT_QUESTION.get(), limit=12)


class _QuestionContext:
    def __init__(self) -> None:
        from contextvars import ContextVar
        self._var = ContextVar("osm_business_question", default="")

    def set(self, value: str):
        return self._var.set(value)

    def reset(self, token) -> None:
        self._var.reset(token)

    def get(self) -> str:
        return self._var.get()


_CURRENT_QUESTION = _QuestionContext()


async def discover_osm_businesses(question: str, *, limit: int = 12, timeout_seconds: float = 5.5) -> list[MapPlace]:
    key = " ".join(normalized_question(question).split())[:300]
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] <= _CACHE_TTL_SECONDS:
        return list(cached[1])[:limit]

    query = build_overpass_query(question)
    if not query:
        return []

    token = _CURRENT_QUESTION.set(question)
    tasks = [asyncio.create_task(_one(endpoint, query, timeout_seconds)) for endpoint in _ENDPOINTS]
    rows: list[MapPlace] = []
    try:
        for future in asyncio.as_completed(tasks):
            try:
                candidate = await future
            except Exception:
                candidate = []
            if candidate:
                rows = candidate
                break
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        _CURRENT_QUESTION.reset(token)

    _CACHE[key] = (now, list(rows))
    return rows[:limit]
