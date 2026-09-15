from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from urllib.parse import quote, quote_plus, urlsplit

from app.services.discovery import DiscoveryError, SearchHit, canonical_result_url
from app.services.public_maps_discovery import MapPlace
from app.services.response_strategy import normalized_question
from app.services.russian_maps_discovery import RussianMapsDiscovery
from app.services.yandex_medicine_discovery import discover_yandex_medicine, is_yandex_medicine_url
from app.services.zoon_discovery import discover_zoon


_SELECTION_RE = re.compile(
    r"\b(?:лучши\w*|топ|рейтинг\w*|найди\w*|подбер\w*|посовет\w*|порекоменду\w*|покажи\w*)\b",
    re.I,
)
_LOCATION_RE = re.compile(
    r"\b(?:в|во|рядом|поблизости|около)\s+[а-яёa-z][а-яёa-z-]{2,}(?:\s+[а-яёa-z][а-яёa-z-]{2,}){0,2}\b",
    re.I,
)
_BUSINESS_HINT_RE = re.compile(
    r"\b(?:компани\w*|центр\w*|клиник\w*|сервис\w*|магазин\w*|салон\w*|"
    r"стоматолог\w*|слухопротезирован\w*|аптек\w*|ресторан\w*|кафе|бар\w*|отел\w*|гостиниц\w*|"
    r"школ\w*|курс\w*|студи\w*|агентств\w*|юрист\w*|адвокат\w*|автосервис\w*|автосалон\w*|"
    r"фитнес\w*|спортзал\w*|парикмах\w*|барбершоп\w*|ремонт\w*|мастер\w*|ветеринар\w*|"
    r"лаборатор\w*|медицин\w*|риелтор\w*|недвижимост\w*|бухгалтер\w*|банк\w*|страхов\w*|"
    r"турагент\w*|туроператор\w*|строительн\w*|доставк\w*|типограф\w*|фотограф\w*|коворкинг\w*|"
    r"саун\w*|бан[яи]\w*|оптик\w*|автомойк\w*|шиномонтаж\w*|детейлинг\w*|нотариус\w*|"
    r"образован\w*|учебн\w*|детсад\w*|детск\w+\s+сад\w*|ателье\w*|химчистк\w*)\b",
    re.I,
)
_MEDICAL_RE = re.compile(
    r"\b(?:медицин\w*|клиник\w*|стоматолог\w*|врач\w*|доктор\w*|сурдолог\w*|"
    r"слухопротезирован\w*|слухов\w+\s+аппарат\w*|лаборатор\w*|диагност\w*)\b",
    re.I,
)
_GENERIC_TITLE = re.compile(
    r"\b(?:яндекс\s*карты|yandex\s*maps|яндекс\s*медицина|2гис|2gis|zoon|зун|карты|maps|"
    r"официальный\s*сайт|отзывы|адрес|телефон|москва|moscow)\b",
    re.I,
)
_WORD = re.compile(r"[a-zа-яё0-9]+", re.I)
_SPACE = re.compile(r"\s+")
_STOP = {
    "лучшие", "лучший", "лучших", "топ", "рейтинг", "найди", "подбери", "посоветуй", "порекомендуй", "покажи",
    "компания", "компании", "компаний", "центр", "центры", "центров", "клиника", "клиники",
    "в", "во", "на", "рядом", "поблизости", "около", "москва", "москве", "москвы",
}
_CITY_SLUGS = {
    "москва": "moscow", "москве": "moscow", "москвы": "moscow",
    "санкт-петербург": "spb", "санкт-петербурге": "spb", "петербург": "spb", "спб": "spb",
    "казань": "kazan", "казани": "kazan", "екатеринбург": "ekaterinburg", "екатеринбурге": "ekaterinburg",
    "новосибирск": "novosibirsk", "новосибирске": "novosibirsk", "самара": "samara", "самаре": "samara",
    "челябинск": "chelyabinsk", "челябинске": "chelyabinsk", "красноярск": "krasnoyarsk", "красноярске": "krasnoyarsk",
    "тюмень": "tyumen", "тюмени": "tyumen", "уфа": "ufa", "уфе": "ufa", "пермь": "perm", "перми": "perm", "сочи": "sochi",
    "калининград": "kaliningrad", "калининграде": "kaliningrad", "воронеж": "voronezh", "воронеже": "voronezh",
    "краснодар": "krasnodar", "краснодаре": "krasnodar", "омск": "omsk", "омске": "omsk",
    "нижний новгород": "nnovgorod", "нижнем новгороде": "nnovgorod", "ростов-на-дону": "rostov", "ростове-на-дону": "rostov",
}
_RU_MAPS = RussianMapsDiscovery(timeout_seconds=3.8)


@dataclass(frozen=True)
class LocalBusinessResult:
    text: str
    sources: list[dict]
    searched: int


def is_local_business_question(question: str) -> bool:
    text = normalized_question(question)
    if not text:
        return False
    return bool(_SELECTION_RE.search(text) and _LOCATION_RE.search(text) and _BUSINESS_HINT_RE.search(text))


def _host(url: str) -> str:
    return (urlsplit(str(url or "")).hostname or "").casefold().removeprefix("www.")


def _kind(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = (parsed.path or "").casefold()
    if host.endswith(("yandex.ru", "yandex.com")) and (
        path.startswith("/maps") or path.startswith("/profile/") or path.startswith("/medicine/clinic/")
    ):
        return "yandex_maps"
    if host.endswith("2gis.ru"):
        return "2gis"
    if host == "zoon.ru":
        return "zoon"
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


def _map_search_links(name: str, *, city_slug: str, address: str = "") -> dict[str, str]:
    query = " ".join(part for part in (name, address) if part).strip()
    return {
        "yandex": f"https://yandex.ru/maps/?text={quote_plus(query)}",
        "2gis": f"https://2gis.ru/{city_slug}/search/{quote(query, safe='')}",
    }


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


async def _search(discovery, query: str, *, timeout: float = 3.4) -> list[SearchHit]:
    try:
        return await asyncio.wait_for(
            discovery.search(query, count=10, country="RU", language="ru"),
            timeout=timeout,
        )
    except (DiscoveryError, TimeoutError, asyncio.TimeoutError):
        return []


async def _empty_places() -> list[MapPlace]:
    return []


def _entity_score(row: dict) -> float:
    map_evidence = sum(bool(row.get(k)) for k in ("yandex_maps", "2gis"))
    zoon_evidence = 1 if row.get("zoon") else 0
    independent = map_evidence + zoon_evidence + (1 if row.get("web") else 0)
    best_rating = max((value for value in row.get("ratings", {}).values() if isinstance(value, (int, float))), default=0.0)
    best_reviews = max((value for value in row.get("reviews", {}).values() if isinstance(value, int)), default=0)
    review_confidence = min(math.log1p(best_reviews) / math.log(1001), 1.0) if best_reviews > 0 else 0.0
    rating_quality = max(0.0, min((best_rating - 3.5) / 1.5, 1.0)) if best_rating else 0.0
    completeness = sum(bool(row.get(k)) for k in ("address", "phone", "website", "web")) / 4.0
    return map_evidence * 2.2 + zoon_evidence * 1.35 + min(independent, 3) * 0.3 + rating_quality * 1.5 + review_confidence + completeness * 0.5


def _empty_entity(name: str) -> dict:
    return {
        "name": name,
        "address": "",
        "phone": "",
        "website": "",
        "web": "",
        "yandex_maps": "",
        "2gis": "",
        "zoon": "",
        "ratings": {},
        "reviews": {},
    }


def _merge_place(entities: dict[str, dict], place: MapPlace) -> None:
    name = _clean_title(place.name)
    key = _entity_key(name)
    if not key:
        return
    row = entities.setdefault(key, _empty_entity(name))
    if place.address and not row["address"]:
        row["address"] = place.address
    if place.phone and not row["phone"]:
        row["phone"] = place.phone
    if place.website and not row["website"]:
        row["website"] = place.website
    if place.provider in {"yandex_maps", "2gis", "zoon"}:
        row[place.provider] = row[place.provider] or place.card_url
    if place.rating is not None:
        row["ratings"][place.provider] = place.rating
    if place.reviews is not None:
        row["reviews"][place.provider] = place.reviews


async def resolve_local_business(question: str, discovery) -> LocalBusinessResult | None:
    if not is_local_business_question(question):
        return None

    city_slug = _city_slug(question)
    medical = bool(_MEDICAL_RE.search(normalized_question(question)))

    # Independent Russian sources run concurrently. Yandex Medicine is a
    # specialization used only for medical queries; Zoon is category-agnostic.
    queries = (
        question,
        f"site:yandex.ru/maps/org/ {question}",
        f"site:2gis.ru/{city_slug}/firm/ {question}",
    )
    maps_task = asyncio.create_task(_RU_MAPS.search(question))
    zoon_task = asyncio.create_task(discover_zoon(question, discovery, limit=8))
    medicine_task = asyncio.create_task(discover_yandex_medicine(question, discovery, limit=8) if medical else _empty_places())
    serp_tasks = [asyncio.create_task(_search(discovery, q)) for q in queries]
    public_rows, zoon_rows, medicine_rows, *serp_batches = await asyncio.gather(
        maps_task, zoon_task, medicine_task, *serp_tasks
    )

    entities: dict[str, dict] = {}
    source_rows: list[dict] = []

    # Prefer structured/direct sources before generic SERP rows.
    for place in medicine_rows:
        _merge_place(entities, place)
        source_rows.append(place.public_source())
    for place in public_rows:
        _merge_place(entities, place)
        source_rows.append(place.public_source())
    for place in zoon_rows:
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
            row = entities.setdefault(key, _empty_entity(name))
            kind = _kind(hit.url)
            if kind in {"yandex_maps", "2gis", "zoon"}:
                row[kind] = row[kind] or hit.url
            elif not row["web"]:
                row["web"] = hit.url

            if is_yandex_medicine_url(hit.url) and not row["yandex_maps"]:
                row["yandex_maps"] = hit.url

    rows = list(entities.values())
    rows = [row for row in rows if row.get("yandex_maps") or row.get("2gis") or row.get("zoon") or row.get("web")]
    rows.sort(key=_entity_score, reverse=True)
    rows = rows[:7]

    if not rows:
        return LocalBusinessResult(
            text=(
                "Не удалось получить подтверждённые организации из Яндекса, 2ГИС, Zoon или поисковой выдачи. "
                "Я не буду придумывать компании."
            ),
            sources=[],
            searched=0,
        )

    out = [
        "Подобрал подтверждённые варианты по Яндексу, 2ГИС, Zoon и открытой поисковой выдаче. "
        "Прямые карточки показываю только когда они реально найдены; иначе даю поиск на карте по подтверждённому названию."
    ]

    for index, row in enumerate(rows, start=1):
        name = row["name"]
        fallback = _map_search_links(name, city_slug=city_slug, address=row.get("address", ""))
        out.append(f"\n{index}. **{name}**")
        if row.get("address"):
            out.append(f"Адрес: {row['address']}")
        if row.get("phone"):
            out.append(f"Телефон: {row['phone']}")
        website = row.get("website") or row.get("web")
        if website:
            out.append("Сайт/источник: " + _md("открыть", website))

        yr = row["ratings"].get("yandex_maps")
        yrc = row["reviews"].get("yandex_maps")
        dr = row["ratings"].get("2gis")
        drc = row["reviews"].get("2gis")
        zr = row["ratings"].get("zoon")
        zrc = row["reviews"].get("zoon")
        if yr is not None:
            out.append(f"Яндекс: {yr:g}" + (f" · {yrc} отзывов" if yrc is not None else ""))
        if dr is not None:
            out.append(f"2ГИС: {dr:g}" + (f" · {drc} отзывов" if drc is not None else ""))
        if zr is not None:
            out.append(f"Zoon: {zr:g}" + (f" · {zrc} отзывов/оценок" if zrc is not None else ""))

        out.append(
            "Яндекс: " + _md("карточка" if row["yandex_maps"] else "поиск на карте", row["yandex_maps"] or fallback["yandex"])
        )
        out.append(
            "2ГИС: " + _md("карточка" if row["2gis"] else "поиск на карте", row["2gis"] or fallback["2gis"])
        )
        if row.get("zoon"):
            out.append("Zoon: " + _md("карточка", row["zoon"]))

    return LocalBusinessResult(
        text="\n".join(out),
        sources=source_rows[:15],
        searched=len(source_rows),
    )
