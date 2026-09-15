from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.services.discovery import DiscoveryError, SearchHit, canonical_result_url
from app.services.public_maps_discovery import MapPlace, PublicMapsDiscovery, map_search_links
from app.services.response_strategy import normalized_question


_LOCAL_RE = re.compile(
    r"(?:\b(?:лучши\w*|топ|рейтинг|найди|подбери|посовет\w*)\b.{0,120}"
    r"\b(?:компани\w*|центр\w*|клиник\w*|сервис\w*|магазин\w*|салон\w*|"
    r"стоматолог\w*|слухопротезирован\w*|аптек\w*|ресторан\w*|кафе|отел\w*)\b|"
    r"\b(?:компани\w*|центр\w*|клиник\w*|сервис\w*|магазин\w*|салон\w*|"
    r"стоматолог\w*|слухопротезирован\w*|аптек\w*|ресторан\w*|кафе|отел\w*)\b.{0,100}"
    r"\b(?:в|рядом|поблизости)\s+[а-яёa-z-]+)",
    re.I,
)
_GENERIC_TITLE = re.compile(
    r"\b(?:яндекс\s*карты|yandex\s*maps|google\s*maps|2гис|2gis|карты|maps|"
    r"официальный\s*сайт|отзывы|адрес|телефон|москва|moscow)\b",
    re.I,
)
_WORD = re.compile(r"[a-zа-яё0-9]+", re.I)
_SPACE = re.compile(r"\s+")
_STOP = {
    "лучшие", "лучший", "лучших", "топ", "рейтинг", "найди", "подбери", "посоветуй",
    "компания", "компании", "компаний", "центр", "центры", "центров", "клиника", "клиники",
    "в", "во", "на", "рядом", "поблизости", "москва", "москве", "москвы",
}
_CITY_SLUGS = {
    "москва": "moscow", "москве": "moscow", "москвы": "moscow",
    "санкт-петербург": "spb", "санкт-петербурге": "spb", "петербург": "spb", "спб": "spb",
    "казань": "kazan", "казани": "kazan", "екатеринбург": "ekaterinburg", "екатеринбурге": "ekaterinburg",
    "новосибирск": "novosibirsk", "новосибирске": "novosibirsk", "самара": "samara", "самаре": "samara",
    "челябинск": "chelyabinsk", "челябинске": "chelyabinsk", "красноярск": "krasnoyarsk", "красноярске": "krasnoyarsk",
    "тюмень": "tyumen", "тюмени": "tyumen", "уфа": "ufa", "уфе": "ufa", "пермь": "perm", "перми": "perm", "сочи": "sochi",
}
_PUBLIC_MAPS = PublicMapsDiscovery(timeout_seconds=4.2)


@dataclass(frozen=True)
class LocalBusinessResult:
    text: str
    sources: list[dict]
    searched: int


def is_local_business_question(question: str) -> bool:
    return bool(_LOCAL_RE.search(normalized_question(question)))


def _host(url: str) -> str:
    return (urlsplit(str(url or "")).hostname or "").casefold().removeprefix("www.")


def _kind(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = (parsed.path or "").casefold()
    if host.endswith(("yandex.ru", "yandex.com")) and path.startswith("/maps"):
        return "yandex_maps"
    if host.endswith("2gis.ru"):
        return "2gis"
    if "google." in host and path.startswith("/maps"):
        return "google_maps"
    return "web"


def _clean_title(value: str) -> str:
    text = _SPACE.sub(" ", str(value or "")).strip(" -–—|·:,.\t\n")
    for sep in (" — ", " | ", " - ", " · "):
        if sep in text:
            left, right = text.split(sep, 1)
            if len(left.strip()) >= 3 and _GENERIC_TITLE.search(right):
                text = left.strip()
                break
    text = _GENERIC_TITLE.sub(" ", text)
    text = _SPACE.sub(" ", text).strip(" -–—|·:,.\t\n")
    return text[:140]


def _stem(value: str) -> str:
    value = value.casefold()
    return value[:7] if len(value) >= 7 else value


def _query_terms(question: str) -> tuple[str, ...]:
    terms: list[str] = []
    for token in _WORD.findall(normalized_question(question)):
        if len(token) < 4 or token in _STOP or token.isdigit():
            continue
        stem = _stem(token)
        if stem not in terms:
            terms.append(stem)
    return tuple(terms[:6])


def _relevant(hit: SearchHit, question: str) -> bool:
    terms = _query_terms(question)
    if not terms:
        return True
    haystack = " ".join((str(hit.title or ""), str(hit.snippet or ""), str(hit.url or ""))).casefold()
    matches = sum(1 for term in terms if term in haystack)
    return matches >= (1 if _kind(hit.url) != "web" else min(2, len(terms)))


def _entity_key(title: str) -> str:
    value = _clean_title(title).casefold()
    value = re.sub(r"[^a-zа-яё0-9]+", " ", value)
    tokens = [t for t in value.split() if len(t) >= 2]
    return " ".join(tokens[:8])


def _city_slug(question: str) -> str:
    text = normalized_question(question)
    for alias, slug in _CITY_SLUGS.items():
        if alias in text:
            return slug
    return "moscow"


def _source_row(hit: SearchHit) -> dict:
    return {
        "title": _clean_title(hit.title) or hit.title or _host(hit.url),
        "url": hit.url,
        "domain": _host(hit.url),
        "provider": hit.provider,
        "snippet": str(hit.snippet or "")[:300],
    }


def _md(label: str, url: str) -> str:
    safe = str(url or "").replace(")", "%29")
    return f"[{label}]({safe})"


async def _search(discovery, query: str, *, timeout: float = 3.0) -> list[SearchHit]:
    try:
        return await asyncio.wait_for(
            discovery.search(query, count=8, country="RU", language="ru"),
            timeout=timeout,
        )
    except (DiscoveryError, TimeoutError, asyncio.TimeoutError):
        return []


def _entity_score(row: dict) -> float:
    direct_maps = sum(bool(row.get(k)) for k in ("yandex_maps", "2gis", "google_maps"))
    best_rating = max((value for value in row.get("ratings", {}).values() if isinstance(value, (int, float))), default=0.0)
    best_reviews = max((value for value in row.get("reviews", {}).values() if isinstance(value, int)), default=0)
    review_confidence = min(math.log1p(best_reviews) / math.log(1001), 1.0) if best_reviews > 0 else 0.0
    rating_quality = max(0.0, min((best_rating - 3.5) / 1.5, 1.0)) if best_rating else 0.0
    completeness = sum(bool(row.get(k)) for k in ("address", "phone", "website", "web")) / 4.0
    return direct_maps * 2.0 + rating_quality * 1.5 + review_confidence + completeness * 0.5


def _merge_place(entities: dict[str, dict], place: MapPlace) -> None:
    name = _clean_title(place.name)
    key = _entity_key(name)
    if not key:
        return
    row = entities.setdefault(key, {
        "name": name,
        "address": "",
        "phone": "",
        "website": "",
        "web": "",
        "yandex_maps": "",
        "2gis": "",
        "google_maps": "",
        "ratings": {},
        "reviews": {},
    })
    if place.address and not row["address"]:
        row["address"] = place.address
    if place.phone and not row["phone"]:
        row["phone"] = place.phone
    if place.website and not row["website"]:
        row["website"] = place.website
    if place.provider in {"yandex_maps", "2gis", "google_maps"}:
        row[place.provider] = row[place.provider] or place.card_url
    if place.rating is not None:
        row["ratings"][place.provider] = place.rating
    if place.reviews is not None:
        row["reviews"][place.provider] = place.reviews


async def resolve_local_business(question: str, discovery) -> LocalBusinessResult | None:
    if not is_local_business_question(question):
        return None

    # Direct public map pages and ordinary SERP run in parallel. This keeps the
    # whole local-business path bounded by the slower of the two, rather than
    # making the user wait for one fallback after another.
    queries = (
        question,
        f'site:yandex.ru/maps/org/ {question}',
        f'site:2gis.ru/ {question}',
        f'site:google.com/maps/place/ {question}',
    )
    public_task = asyncio.create_task(_PUBLIC_MAPS.search(question))
    serp_tasks = [asyncio.create_task(_search(discovery, q)) for q in queries]
    public_rows, *serp_batches = await asyncio.gather(public_task, *serp_tasks)

    entities: dict[str, dict] = {}
    source_rows: list[dict] = []

    # Direct-map entities are authoritative for card URLs: their IDs came from
    # the map page itself, so no LLM guessing or synthetic card URLs are needed.
    for place in public_rows:
        _merge_place(entities, place)
        source_rows.append(place.public_source())

    seen_urls = {canonical_result_url(row["url"]) for row in source_rows if row.get("url")}
    for batch in serp_batches:
        for hit in batch:
            if not _relevant(hit, question):
                continue
            canonical = canonical_result_url(hit.url)
            if not canonical or canonical in seen_urls:
                continue
            seen_urls.add(canonical)
            source_rows.append(_source_row(hit))
            name = _clean_title(hit.title)
            key = _entity_key(name)
            if not key:
                continue
            row = entities.setdefault(key, {
                "name": name,
                "address": "",
                "phone": "",
                "website": "",
                "web": "",
                "yandex_maps": "",
                "2gis": "",
                "google_maps": "",
                "ratings": {},
                "reviews": {},
            })
            kind = _kind(hit.url)
            if kind in {"yandex_maps", "2gis", "google_maps"}:
                row[kind] = row[kind] or hit.url
            elif not row["web"]:
                row["web"] = hit.url

    rows = list(entities.values())
    rows = [row for row in rows if row.get("yandex_maps") or row.get("2gis") or row.get("google_maps") or row.get("web")]
    rows.sort(key=_entity_score, reverse=True)
    rows = rows[:6]

    if not rows:
        return LocalBusinessResult(
            text=(
                "Не удалось получить подтверждённые карточки организаций из Яндекс Карт, Google Maps, 2ГИС или поисковой выдачи. "
                "Я не буду придумывать компании. Попробуйте повторить запрос позже."
            ),
            sources=[],
            searched=0,
        )

    city_slug = _city_slug(question)
    out = [
        "Подобрал варианты по публичным данным карт и поисковой выдачи. "
        "Ссылка «карточка» ведёт прямо на найденную карточку организации; «поиск на карте» используется только когда прямой ID получить не удалось."
    ]

    for index, row in enumerate(rows, start=1):
        name = row["name"]
        fallback = map_search_links(name, city_slug=city_slug, address=row.get("address", ""))
        out.append(f"\n{index}. **{name}**")
        if row.get("address"):
            out.append(f"Адрес: {row['address']}")
        if row.get("phone"):
            out.append(f"Телефон: {row['phone']}")
        website = row.get("website") or row.get("web")
        if website:
            out.append("Сайт: " + _md("открыть", website))

        yr = row["ratings"].get("yandex_maps")
        yrc = row["reviews"].get("yandex_maps")
        dr = row["ratings"].get("2gis")
        drc = row["reviews"].get("2gis")
        gr = row["ratings"].get("google_maps")
        grc = row["reviews"].get("google_maps")
        if yr is not None:
            out.append(f"Яндекс: {yr:g}" + (f" · {yrc} отзывов" if yrc is not None else ""))
        if dr is not None:
            out.append(f"2ГИС: {dr:g}" + (f" · {drc} отзывов" if drc is not None else ""))
        if gr is not None:
            out.append(f"Google: {gr:g}" + (f" · {grc} отзывов" if grc is not None else ""))

        out.append(
            "Яндекс Карты: " + _md("карточка" if row["yandex_maps"] else "поиск на карте", row["yandex_maps"] or fallback["yandex"])
        )
        out.append(
            "Google Maps: " + _md("карточка" if row["google_maps"] else "поиск на карте", row["google_maps"] or fallback["google"])
        )
        out.append(
            "2ГИС: " + _md("карточка" if row["2gis"] else "поиск на карте", row["2gis"] or fallback["2gis"])
        )

    return LocalBusinessResult(
        text="\n".join(out),
        sources=source_rows[:12],
        searched=len(source_rows),
    )
